#!/usr/bin/env python3
"""
gallery.py — Perchance Gallery (enhanced)

Aggregates every image dropped by installed generators into a single
tag-searchable, filename-free gallery.

Features
--------
* 3 selectable cell aspect ratios: 1x1, 1.5x1, 1x1.5
* Tag sidebar with checkboxes — multi-tag filtering, ALL (AND) or ANY (OR)
* Resolution filter: Any / 512x512 / 768x768 / 512x768 / 768x512 / Other
* Content rating filter — general / sensitive / questionable / explicit /
  unrated.  A rating is ONE value per image, so unlike tags it lives in its
  own column with its own checkbox group (checking none means "no filter").
* Optional local AI, via imagetools.py (needs onnxruntime; entirely optional):
    - auto-tagging with wd-swinv2-tagger-v3, which also sets the rating
    - Real-ESRGAN style upscaling, tiled so RAM stays bounded
  Available per image from the right-click menu, in bulk over a selection, and
  optionally automatic for newly imported images (settings → Image tools).
* Full-screen "spotlight" viewer (arrow-key navigation, zoom, info overlay)
* Per-image metadata: Prompt, Guidance Scale, Seed (all optional)
* Edit dialog: add AND remove tags, edit metadata, bulk-apply to a selection
* Global tag management: rename / delete a tag everywhere

Depends on config.py (same one launcher.py uses) for:
    config.APP_ROOT, config.DATA_DIR, config.list_generators()

Note: all generator images are pooled directly under program_root/data/
(NOT split into per-slug data/<slug>/files/ subfolders), so this scans
config.DATA_DIR as one flat tree.
"""

import sys
import os
import sqlite3
import hashlib
import subprocess
from pathlib import Path

# program root (where config.py / configurate.py live) plus its parent, so this
# runs whether it sits in the root or one folder below it
_HERE = Path(__file__).parent.resolve()
for _p in (_HERE, _HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import config

# Optional local-AI helpers. Missing onnxruntime, missing models, or a missing
# imagetools.py all degrade to "the AI menu entries simply aren't there".
try:
    import imagetools
except Exception:                       # noqa: BLE001 - imagetools optional
    imagetools = None

from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal, QPoint, QEvent
from PyQt6.QtGui import (
    QPixmap, QIcon, QImage, QImageReader, QKeySequence, QShortcut,
    QPainter, QColor, QGuiApplication
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QListWidget, QListWidgetItem, QLabel,
    QFrame, QMenu, QInputDialog, QCompleter, QSizePolicy, QComboBox,
    QDialog, QDialogButtonBox, QPlainTextEdit, QFormLayout, QGridLayout,
    QMessageBox, QScrollArea, QStatusBar, QSplitter, QToolButton,
    QAbstractItemView, QCheckBox, QProgressDialog, QGroupBox
)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
GALLERY_DIR = config.DATA_DIR / "_gallery"
THUMB_DIR = GALLERY_DIR / "thumbs"
DB_PATH = GALLERY_DIR / "gallery.db"

# ─── Settings bridge ─────────────────────────────────────────────────────────
# Preferences live in settings.json and are edited with configurate.py.
# If that file is missing this falls back to the values passed in, so the
# gallery still runs standalone.
try:
    from configurate import settings as _settings

    def setting(key, default=None):
        value = _settings.get(key, default)
        return default if value is None else value

    def reload_settings():
        try:
            _settings.load()
        except Exception:
            pass
except Exception:                       # noqa: BLE001 - configurate optional
    def setting(key, default=None):
        return default

    def reload_settings():
        pass


def view_modes(base=None):
    """Grid cell sizes derived from the configured thumbnail size."""
    base = max(120, min(420, int(base or setting("gallery_thumb_size", 220))))
    short = max(90, round(base * 2 / 3))
    return {
        "1x1":   (base, base),
        "1.5x1": (base, short),
        "1x1.5": (short, base),
    }


VIEW_MODES = view_modes()

# (label, width, height) — None,None means the special "Any" / "Other" entries
RESOLUTIONS = [
    ("Any", None, None),
    ("512 × 512", 512, 512),
    ("768 × 768", 768, 768),
    ("512 × 768", 512, 768),
    ("768 × 512", 768, 512),
    ("Other…", -1, -1),
]
KNOWN_RES = [(512, 512), (768, 768), (512, 768), (768, 512)]

# ─── Content ratings ───────────────────────────────────────────────────────
# Deliberately NOT tags: exactly one applies to an image at a time, so it gets
# its own column, its own filter group, and its own "not looked at yet" state.
RATINGS = ["general", "sensitive", "questionable", "explicit"]
UNRATED = "unrated"
RATING_FILTER_ORDER = RATINGS + [UNRATED]
RATING_LABELS = {
    "general":      "General",
    "sensitive":    "Sensitive",
    "questionable": "Questionable",
    "explicit":     "Explicit",
    UNRATED:        "Unrated",
}
RATING_COLORS = {
    "general":      "#6fb98f",
    "sensitive":    "#d9b45b",
    "questionable": "#dd8f52",
    "explicit":     "#e0806f",
    UNRATED:        "#8d8b88",
}


# ─── Database layer ────────────────────────────────────────────────────────

def get_db():
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS images(
        id INTEGER PRIMARY KEY,
        filepath TEXT UNIQUE,
        hash TEXT,
        source_app TEXT,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS tags(
        id INTEGER PRIMARY KEY,
        name TEXT UNIQUE COLLATE NOCASE
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS image_tags(
        image_id INTEGER,
        tag_id INTEGER,
        PRIMARY KEY(image_id, tag_id)
    )""")
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn):
    """Add the metadata / resolution / rating columns to older databases."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(images)")}
    for col, decl in [
        ("width", "INTEGER"),
        ("height", "INTEGER"),
        ("prompt", "TEXT"),
        ("guidance_scale", "TEXT"),
        ("seed", "TEXT"),
        # NULL / "" means unrated — nothing has looked at the image yet
        ("rating", "TEXT"),
        ("rated_at", "TEXT"),
        ("rating_source", "TEXT"),      # "ai" or "manual"
        ("upscaled_from", "INTEGER"),   # id of the image this was upscaled from
    ]:
        if col not in have:
            conn.execute(f"ALTER TABLE images ADD COLUMN {col} {decl}")

    # provenance for tags, so AI suggestions can be told apart from your own
    have_it = {r[1] for r in conn.execute("PRAGMA table_info(image_tags)")}
    if "auto" not in have_it:
        conn.execute("ALTER TABLE image_tags ADD COLUMN auto INTEGER DEFAULT 0")
    if "score" not in have_it:
        conn.execute("ALTER TABLE image_tags ADD COLUMN score REAL")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_it_tag ON image_tags(tag_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_it_img ON image_tags(image_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_img_wh ON images(width, height)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_img_rating ON images(rating)")


def file_hash(path: Path, chunk_size=65536) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def read_size(path: Path):
    """Cheap header-only dimension read (thread-safe, no QPixmap)."""
    reader = QImageReader(str(path))
    size = reader.size()
    if size.isValid():
        return size.width(), size.height()
    return None, None


def scan_all_generators(conn):
    """Walk config.DATA_DIR directly — all generator images are pooled together
    right under program_root/data/, not split per-slug into files/ subfolders.
    Skips the gallery's own _gallery/ folder (db + thumbnail cache) so it doesn't
    re-ingest its own generated thumbnails. Also backfills missing dimensions."""
    cur = conn.cursor()
    known_slugs = set(config.list_generators())
    data_dir = config.DATA_DIR
    new_ids = []
    if not data_dir.exists():
        return new_ids

    for root, dirs, files in os.walk(data_dir):
        root_path = Path(root)
        # never descend into our own gallery db/thumbs folder
        if GALLERY_DIR == root_path or GALLERY_DIR in root_path.parents:
            dirs[:] = []
            continue

        for fname in files:
            if Path(fname).suffix.lower() not in IMAGE_EXTS:
                continue
            fpath = root_path / fname
            cur.execute("SELECT id FROM images WHERE filepath=?", (str(fpath),))
            if cur.fetchone():
                continue
            try:
                h = file_hash(fpath)
            except OSError:
                continue

            w, ht = read_size(fpath)

            # best-effort: tag with generator slug if the path happens to sit
            # under a folder matching a known slug, else leave source_app "unknown"
            source_app = "unknown"
            try:
                rel_parts = fpath.relative_to(data_dir).parts
                if rel_parts and rel_parts[0] in known_slugs:
                    source_app = rel_parts[0]
            except ValueError:
                pass

            cur.execute(
                "INSERT OR IGNORE INTO images(filepath, hash, source_app, width, height)"
                " VALUES (?,?,?,?,?)",
                (str(fpath), h, source_app, w, ht)
            )
            img_id = cur.lastrowid
            new_ids.append(img_id)
            if source_app != "unknown":
                tag_id = ensure_tag(conn, source_app)
                cur.execute(
                    "INSERT OR IGNORE INTO image_tags(image_id, tag_id) VALUES (?,?)",
                    (img_id, tag_id)
                )

    # backfill dimensions for rows added by an older version of the gallery
    for img_id, fp in conn.execute(
        "SELECT id, filepath FROM images WHERE width IS NULL OR height IS NULL"
    ).fetchall():
        p = Path(fp)
        if not p.exists():
            continue
        w, ht = read_size(p)
        if w:
            conn.execute("UPDATE images SET width=?, height=? WHERE id=?", (w, ht, img_id))

    conn.commit()
    return new_ids


def prune_missing(conn):
    """Drop rows whose file no longer exists on disk."""
    gone = [r[0] for r in conn.execute("SELECT id, filepath FROM images").fetchall()
            if not Path(r[1]).exists()]
    for img_id in gone:
        conn.execute("DELETE FROM image_tags WHERE image_id=?", (img_id,))
        conn.execute("DELETE FROM images WHERE id=?", (img_id,))
    conn.commit()
    return len(gone)


# ─── Tag helpers ───────────────────────────────────────────────────────────

def ensure_tag(conn, name: str) -> int:
    name = name.strip().lower()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
    cur.execute("SELECT id FROM tags WHERE name=?", (name,))
    return cur.fetchone()[0]


def add_tag_to_image(conn, image_id: int, tag_name: str, auto=False, score=None,
                     commit=True):
    """Attach a tag. auto=True marks it as an AI suggestion; a tag you added
    yourself is never downgraded to auto by a later auto-tag run."""
    if not tag_name.strip():
        return
    tag_id = ensure_tag(conn, tag_name)
    conn.execute(
        "INSERT OR IGNORE INTO image_tags(image_id, tag_id, auto, score) VALUES (?,?,?,?)",
        (image_id, tag_id, 1 if auto else 0, score)
    )
    if not auto:
        # promote an existing AI suggestion to a confirmed tag
        conn.execute("UPDATE image_tags SET auto=0 WHERE image_id=? AND tag_id=?",
                     (image_id, tag_id))
    if commit:
        conn.commit()


def remove_tag_from_image(conn, image_id: int, tag_name: str):
    conn.execute("""
        DELETE FROM image_tags
        WHERE image_id = ?
          AND tag_id IN (SELECT id FROM tags WHERE name = ?)
    """, (image_id, tag_name.strip().lower()))
    conn.commit()


def delete_tag_everywhere(conn, tag_name: str):
    row = conn.execute("SELECT id FROM tags WHERE name=?", (tag_name.lower(),)).fetchone()
    if not row:
        return
    conn.execute("DELETE FROM image_tags WHERE tag_id=?", (row[0],))
    conn.execute("DELETE FROM tags WHERE id=?", (row[0],))
    conn.commit()


def rename_tag(conn, old: str, new: str):
    new = new.strip().lower()
    if not new:
        return
    old_row = conn.execute("SELECT id FROM tags WHERE name=?", (old.lower(),)).fetchone()
    if not old_row:
        return
    existing = conn.execute("SELECT id FROM tags WHERE name=?", (new,)).fetchone()
    if existing and existing[0] != old_row[0]:
        # merge into the existing tag
        conn.execute(
            "UPDATE OR IGNORE image_tags SET tag_id=? WHERE tag_id=?",
            (existing[0], old_row[0])
        )
        conn.execute("DELETE FROM image_tags WHERE tag_id=?", (old_row[0],))
        conn.execute("DELETE FROM tags WHERE id=?", (old_row[0],))
    else:
        conn.execute("UPDATE tags SET name=? WHERE id=?", (new, old_row[0]))
    conn.commit()


def all_tag_names(conn):
    return [r[0] for r in conn.execute("SELECT name FROM tags ORDER BY name")]


def tag_counts(conn):
    """[(tag_name, image_count), …] sorted by name."""
    return conn.execute("""
        SELECT t.name, COUNT(it.image_id)
        FROM tags t LEFT JOIN image_tags it ON it.tag_id = t.id
        GROUP BY t.id ORDER BY t.name
    """).fetchall()


def get_tags_for_image(conn, image_id: int):
    cur = conn.execute("""
        SELECT t.name FROM tags t
        JOIN image_tags it ON it.tag_id = t.id
        WHERE it.image_id = ?
        ORDER BY t.name
    """, (image_id,))
    return [r[0] for r in cur.fetchall()]


def get_auto_tags_for_image(conn, image_id: int):
    """Just the AI-suggested ones, highest score first."""
    cur = conn.execute("""
        SELECT t.name, it.score FROM tags t
        JOIN image_tags it ON it.tag_id = t.id
        WHERE it.image_id = ? AND IFNULL(it.auto,0) = 1
        ORDER BY IFNULL(it.score,0) DESC, t.name
    """, (image_id,))
    return cur.fetchall()


def clear_auto_tags(conn, image_ids):
    """Drop AI suggestions but keep anything you added or confirmed yourself."""
    n = 0
    for img_id in image_ids:
        cur = conn.execute(
            "DELETE FROM image_tags WHERE image_id=? AND IFNULL(auto,0)=1", (img_id,))
        n += cur.rowcount or 0
    conn.commit()
    return n


# ─── Rating helpers ────────────────────────────────────────────────────

def set_rating(conn, image_id: int, rating, source="manual", commit=True):
    """rating: one of RATINGS, or None/"unrated"/"" to clear it."""
    value = (rating or "").strip().lower()
    if value in ("", UNRATED):
        conn.execute("UPDATE images SET rating=NULL, rated_at=NULL,"
                     " rating_source=NULL WHERE id=?", (image_id,))
    else:
        if value not in RATINGS:
            return
        conn.execute("UPDATE images SET rating=?, rated_at=CURRENT_TIMESTAMP,"
                     " rating_source=? WHERE id=?", (value, source, image_id))
    if commit:
        conn.commit()


def get_rating(conn, image_id: int) -> str:
    row = conn.execute("SELECT rating FROM images WHERE id=?", (image_id,)).fetchone()
    value = (row[0] if row else None) or UNRATED
    return value if value in RATINGS else UNRATED


def rating_counts(conn):
    """{rating: count} across the whole library, including 'unrated'."""
    counts = {key: 0 for key in RATING_FILTER_ORDER}
    for value, n in conn.execute(
        "SELECT IFNULL(NULLIF(rating,''), ?), COUNT(*) FROM images GROUP BY 1",
        (UNRATED,)
    ).fetchall():
        key = (value or UNRATED).lower()
        counts[key if key in counts else UNRATED] += n
    return counts


def get_image_row(conn, image_id: int):
    return conn.execute("""
        SELECT id, filepath, hash, source_app, width, height,
               prompt, guidance_scale, seed, added_at, rating, upscaled_from
        FROM images WHERE id=?
    """, (image_id,)).fetchone()


def delete_image_and_record(conn, image_id: int, delete_file=None):
    """Delete an image and its DB metadata as one logical operation.

    The database is left untouched when removing the file fails. ``delete_file``
    is injectable so the failure path can be tested without relying on OS locks.
    The caller owns the surrounding transaction.
    """
    row = get_image_row(conn, image_id)
    if not row:
        return False, "gallery record no longer exists"

    filepath = Path(row[1])
    remove = delete_file or (lambda path: path.unlink())
    try:
        if filepath.exists():
            remove(filepath)
    except OSError as exc:
        return False, f"{filepath.name}: {exc}"

    img_hash = row[2]
    if img_hash:
        cached_thumb = thumb_path(img_hash)
        try:
            cached_thumb.unlink(missing_ok=True)
        except OSError:
            pass

    conn.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
    conn.execute("DELETE FROM images WHERE id=?", (image_id,))
    return True, ""


def update_metadata(conn, image_id: int, prompt=None, guidance=None, seed=None):
    """Only non-None values are written, so blank fields in a bulk edit
    leave the existing value untouched."""
    sets, vals = [], []
    for col, val in (("prompt", prompt), ("guidance_scale", guidance), ("seed", seed)):
        if val is not None:
            sets.append(f"{col}=?")
            vals.append(val)
    if not sets:
        return
    vals.append(image_id)
    conn.execute(f"UPDATE images SET {', '.join(sets)} WHERE id=?", tuple(vals))
    conn.commit()


# ─── Search ────────────────────────────────────────────────────────────────

def query_images(conn, text="", tags=None, match_all=True, resolution=None,
                 untagged_only=False, ratings=None):
    """Return [(id, filepath, hash), …] newest first.

    text        free-text: space separated terms ANDed, each matched against
                tag names, source_app, prompt and seed
    tags        list of exact tag names selected in the sidebar
    match_all   True  -> image must carry EVERY selected tag (AND)
                False -> image must carry AT LEAST ONE selected tag (OR)
    resolution  None/"any" | (w, h) | "other"
    ratings     list drawn from RATINGS + ["unrated"]; empty or all-selected
                means no rating filter at all
    """
    tags = [t.strip().lower() for t in (tags or []) if t.strip()]
    where, params = [], []

    # --- exact multi-tag filter -------------------------------------------
    if tags:
        placeholders = ",".join("?" * len(tags))
        if match_all:
            where.append(f"""i.id IN (
                SELECT it.image_id FROM image_tags it
                JOIN tags t ON t.id = it.tag_id
                WHERE t.name IN ({placeholders})
                GROUP BY it.image_id
                HAVING COUNT(DISTINCT t.name) = ?
            )""")
            params.extend(tags)
            params.append(len(tags))
        else:
            where.append(f"""i.id IN (
                SELECT it.image_id FROM image_tags it
                JOIN tags t ON t.id = it.tag_id
                WHERE t.name IN ({placeholders})
            )""")
            params.extend(tags)

    # --- free-text terms, ANDed ------------------------------------------
    for term in [t.strip().lower() for t in text.split() if t.strip()]:
        like = f"%{term}%"
        where.append("""(
            i.source_app LIKE ?
            OR IFNULL(i.prompt,'') LIKE ?
            OR IFNULL(i.seed,'') LIKE ?
            OR i.id IN (
                SELECT it.image_id FROM image_tags it
                JOIN tags t ON t.id = it.tag_id
                WHERE t.name LIKE ?
            )
        )""")
        params.extend([like, like, like, like])

    # --- resolution -------------------------------------------------------
    if isinstance(resolution, tuple):
        where.append("i.width = ? AND i.height = ?")
        params.extend([resolution[0], resolution[1]])
    elif resolution == "other":
        clause = " OR ".join(["(i.width = ? AND i.height = ?)"] * len(KNOWN_RES))
        where.append(f"NOT ({clause})")
        for w, h in KNOWN_RES:
            params.extend([w, h])

    # --- content rating (its own column, not a tag) ------------------------
    wanted = [r for r in (ratings or []) if r in RATING_FILTER_ORDER]
    if wanted and len(wanted) < len(RATING_FILTER_ORDER):
        clauses = []
        real = [r for r in wanted if r != UNRATED]
        if real:
            clauses.append("LOWER(IFNULL(i.rating,'')) IN (%s)"
                           % ",".join("?" * len(real)))
            params.extend(real)
        if UNRATED in wanted:
            # anything never rated, or holding a value we don't recognise
            clauses.append("(i.rating IS NULL OR TRIM(i.rating) = '' OR "
                           "LOWER(i.rating) NOT IN (%s))"
                           % ",".join("?" * len(RATINGS)))
            params.extend(RATINGS)
        where.append("(" + " OR ".join(clauses) + ")")

    if untagged_only:
        where.append("i.id NOT IN (SELECT image_id FROM image_tags)")

    sql = "SELECT i.id, i.filepath, i.hash FROM images i"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY i.added_at DESC, i.id DESC"
    return conn.execute(sql, tuple(params)).fetchall()


# ─── Thumbnail cache ────────────────────────────────────────────────────────

def thumb_path(img_hash: str) -> Path:
    return THUMB_DIR / f"{img_hash}.png"


def get_or_make_thumb(filepath: str, img_hash: str, max_dim: int = 0) -> str:
    # generate a little larger than the grid asks for so bumping the thumbnail
    # size in settings doesn't immediately look soft
    max_dim = max_dim or max(320, round(setting("gallery_thumb_size", 220) * 1.6))
    tp = thumb_path(img_hash)
    if tp.exists():
        return str(tp)
    img = QImage(filepath)
    if img.isNull():
        return filepath
    scaled = img.scaled(
        max_dim, max_dim,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation
    )
    scaled.save(str(tp), "PNG")
    return str(tp)


# ─── Background scan worker (keeps UI responsive) ──────────────────────────

class ScanWorker(QThread):
    # carries the ids of the rows it just inserted, so the gallery can hand
    # only genuinely new images to the AI queue
    finished_scan = pyqtSignal(object)

    def run(self):
        conn = get_db()
        new_ids = []
        try:
            new_ids = scan_all_generators(conn) or []
        finally:
            conn.close()
        self.finished_scan.emit(new_ids)


# ─── Flow layout for tag chips ─────────────────────────────────────────────

class TagChip(QFrame):
    """A tag pill with an × button."""

    def __init__(self, name, on_remove, mixed=False, parent=None):
        super().__init__(parent)
        self.name = name
        self.setObjectName("TagChip")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 4, 2)
        lay.setSpacing(4)
        label = QLabel(name + ("  (some)" if mixed else ""))
        label.setStyleSheet("background:transparent;border:none;color:#ffffff;"
                            if not mixed else
                            "background:transparent;border:none;color:#cdccca;")
        lay.addWidget(label)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        btn = QToolButton()
        btn.setText("×")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(f"Remove “{name}”")
        btn.setStyleSheet(
            "QToolButton{border:none;background:transparent;color:#cdccca;"
            "font-size:14px;font-weight:700;padding:0 4px;}"
            "QToolButton:hover{color:#ff7a7a;}"
        )
        btn.clicked.connect(lambda: on_remove(name))
        lay.addWidget(btn)
        self.setStyleSheet(
            "#TagChip{background:#01696f;border:1px solid #018b93;border-radius:10px;}"
            if not mixed else
            "#TagChip{background:#3a3936;border:1px solid #4d4b48;border-radius:10px;}"
        )


# ─── Edit dialog: tags + metadata ──────────────────────────────────────────

class EditDialog(QDialog):
    """Add AND remove tags, plus edit Prompt / Guidance Scale / Seed.

    Works for one image or a multi-image selection. In a multi-selection,
    metadata fields start blank and only the ones you fill in are applied.
    """

    def __init__(self, conn, image_ids, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.image_ids = list(image_ids)
        self.multi = len(self.image_ids) > 1
        self.setWindowTitle(
            "Edit image" if not self.multi else f"Edit {len(self.image_ids)} images"
        )
        self.setMinimumWidth(560)
        self._build()
        self._reload_tags()

    # -- ui ---------------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(14)

        # preview
        self.preview = QLabel()
        self.preview.setFixedSize(180, 180)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(
            "background:#141312;border:1px solid #393836;border-radius:8px;")
        row = get_image_row(self.conn, self.image_ids[0])
        if row:
            pm = QPixmap(get_or_make_thumb(row[1], row[2] or ""))
            if not pm.isNull():
                self.preview.setPixmap(pm.scaled(
                    172, 172, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
        top.addWidget(self.preview)

        # metadata form
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.prompt = QPlainTextEdit()
        self.prompt.setFixedHeight(86)
        self.prompt.setPlaceholderText(
            "Optional — the prompt used to generate this image"
            + ("  (leave blank to keep each image's own prompt)" if self.multi else ""))
        form.addRow("Prompt", self.prompt)

        self.guidance = QLineEdit()
        self.guidance.setPlaceholderText("Optional — e.g. 7.5")
        form.addRow("Guidance Scale", self.guidance)

        self.seed = QLineEdit()
        self.seed.setPlaceholderText("Optional — e.g. 1234567890")
        form.addRow("Seed", self.seed)

        # content rating — one value per image, so a combo rather than chips
        self.rating = QComboBox()
        if self.multi:
            self.rating.addItem("— leave unchanged —", None)
        for key in RATING_FILTER_ORDER:
            label = ("Unrated (clear it)" if key == UNRATED else RATING_LABELS[key])
            self.rating.addItem(label, key)
        if not self.multi:
            current = get_rating(self.conn, self.image_ids[0])
            idx = self.rating.findData(current)
            self.rating.setCurrentIndex(max(0, idx))
        form.addRow("Rating", self.rating)

        info = QLabel()
        info.setWordWrap(True)
        if row and not self.multi:
            dims = f"{row[4]}×{row[5]}" if row[4] else "unknown size"
            info.setText(f"{dims}   ·   source: {row[3] or 'unknown'}   ·   added {row[9]}")
        elif self.multi:
            info.setText(f"{len(self.image_ids)} images selected — tag changes apply to all.")
        info.setStyleSheet("color:#8d8b88;")
        form.addRow("", info)

        top.addLayout(form, 1)
        root.addLayout(top)

        # tags section
        tag_head = QHBoxLayout()
        lbl = QLabel("Tags")
        lbl.setStyleSheet("font-weight:700;")
        tag_head.addWidget(lbl)
        tag_head.addStretch()
        root.addLayout(tag_head)

        self.chip_area = QScrollArea()
        self.chip_area.setWidgetResizable(True)
        self.chip_area.setFixedHeight(110)
        self.chip_area.setStyleSheet(
            "QScrollArea{background:#191817;border:1px solid #393836;border-radius:8px;}")
        self.chip_host = QWidget()
        self.chip_grid = QGridLayout(self.chip_host)
        self.chip_grid.setContentsMargins(8, 8, 8, 8)
        self.chip_grid.setSpacing(6)
        self.chip_area.setWidget(self.chip_host)
        root.addWidget(self.chip_area)

        add_row = QHBoxLayout()
        self.new_tag = QLineEdit()
        self.new_tag.setPlaceholderText("Add tag(s) — comma separated, Enter to add")
        completer = QCompleter(all_tag_names(self.conn), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.new_tag.setCompleter(completer)
        self.new_tag.returnPressed.connect(self._add_typed_tags)
        add_row.addWidget(self.new_tag, 1)
        add_btn = QPushButton("＋ Add")
        add_btn.clicked.connect(self._add_typed_tags)
        add_row.addWidget(add_btn)
        root.addLayout(add_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save metadata")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.accept)
        root.addWidget(buttons)

        self.setStyleSheet("""
            QDialog { background:#1c1b19; color:#cdccca;
                font-family:'Segoe UI',sans-serif; font-size:13px; }
            QLineEdit, QPlainTextEdit { background:#22211f; border:1px solid #393836;
                border-radius:6px; padding:5px 8px; color:#cdccca; }
            QPushButton { background:#22211f; border:1px solid #393836;
                border-radius:6px; padding:5px 12px; }
            QPushButton:hover { border-color:#01696f; }
            QLabel { background:transparent; }
        """)

        # prefill metadata for a single image
        if row and not self.multi:
            self.prompt.setPlainText(row[6] or "")
            self.guidance.setText(row[7] or "")
            self.seed.setText(row[8] or "")

    # -- tag chips ---------------------------------------------------------
    def _tag_state(self):
        """{tag: True if on every selected image else False}"""
        sets = [set(get_tags_for_image(self.conn, i)) for i in self.image_ids]
        union = set().union(*sets) if sets else set()
        common = set.intersection(*sets) if sets else set()
        return {t: (t in common) for t in sorted(union)}

    def _reload_tags(self):
        while self.chip_grid.count():
            item = self.chip_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        state = self._tag_state()
        if not state:
            empty = QLabel("No tags yet — add some below.")
            empty.setStyleSheet("color:#8d8b88;")
            self.chip_grid.addWidget(empty, 0, 0)
            return
        cols = 3
        for idx, (name, on_all) in enumerate(state.items()):
            chip = TagChip(name, self._remove_tag, mixed=not on_all)
            self.chip_grid.addWidget(chip, idx // cols, idx % cols,
                                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.chip_grid.setColumnStretch(cols, 1)
        self.chip_grid.setRowStretch(len(state) // cols + 1, 1)

    def _add_typed_tags(self):
        raw = self.new_tag.text()
        added = False
        for tag in [t.strip() for t in raw.split(",") if t.strip()]:
            for img_id in self.image_ids:
                add_tag_to_image(self.conn, img_id, tag)
            added = True
        if added:
            self.new_tag.clear()
            self.new_tag.setCompleter(self._fresh_completer())
            self._reload_tags()

    def _fresh_completer(self):
        c = QCompleter(all_tag_names(self.conn), self)
        c.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        return c

    def _remove_tag(self, name):
        for img_id in self.image_ids:
            remove_tag_from_image(self.conn, img_id, name)
        self._reload_tags()

    # -- save --------------------------------------------------------------
    def _save(self):
        p = self.prompt.toPlainText()
        g = self.guidance.text().strip()
        s = self.seed.text().strip()
        if self.multi:
            # blank == leave alone when editing many at once
            p = p if p.strip() else None
            g = g or None
            s = s or None
        rating = self.rating.currentData()
        for img_id in self.image_ids:
            update_metadata(self.conn, img_id, prompt=p, guidance=g, seed=s)
            if rating is not None:
                set_rating(self.conn, img_id, rating, source="manual")
        self.accept()


# ─── Tag manager dialog ────────────────────────────────────────────────────

class TagManagerDialog(QDialog):
    """Rename / delete tags across the whole library."""

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("Manage tags")
        self.resize(360, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 12)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        lay.addWidget(self.list, 1)

        btns = QHBoxLayout()
        for text, fn in [("Rename…", self._rename), ("Delete", self._delete)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        lay.addLayout(btns)

        self.setStyleSheet("""
            QDialog { background:#1c1b19; color:#cdccca; font-size:13px; }
            QListWidget { background:#22211f; border:1px solid #393836;
                border-radius:6px; }
            QPushButton { background:#22211f; border:1px solid #393836;
                border-radius:6px; padding:5px 12px; }
        """)
        self._reload()

    def _reload(self):
        self.list.clear()
        for name, count in tag_counts(self.conn):
            it = QListWidgetItem(f"{name}   ({count})")
            it.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(it)

    def _selected(self):
        return [i.data(Qt.ItemDataRole.UserRole) for i in self.list.selectedItems()]

    def _rename(self):
        sel = self._selected()
        if len(sel) != 1:
            QMessageBox.information(self, "Rename tag", "Select exactly one tag to rename.")
            return
        new, ok = QInputDialog.getText(self, "Rename tag", f"New name for “{sel[0]}”:",
                                       text=sel[0])
        if ok and new.strip():
            rename_tag(self.conn, sel[0], new)
            self._reload()

    def _delete(self):
        sel = self._selected()
        if not sel:
            return
        if QMessageBox.question(
            self, "Delete tags",
            "Remove these tags from every image?\n\n" + ", ".join(sel)
        ) == QMessageBox.StandardButton.Yes:
            for name in sel:
                delete_tag_everywhere(self.conn, name)
            self._reload()


# ─── Spotlight full-size viewer ────────────────────────────────────────────

class SpotlightViewer(QDialog):
    """Full-screen image viewer.

    ←/→ or A/D  previous / next        F  fit / 100%
    +/-         zoom                   I  toggle info panel
    Mouse wheel zoom, drag to pan, Esc closes, E opens the edit dialog.
    """

    def __init__(self, conn, entries, index=0, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.entries = entries            # [(id, filepath, hash), …]
        self.index = max(0, min(index, len(entries) - 1))
        self.zoom = None                  # None == fit to window
        self.pan = QPoint(0, 0)
        self._drag_origin = None
        self._pixmap = QPixmap()
        self.show_info = True

        self.setWindowTitle("Spotlight")
        self.setWindowFlag(Qt.WindowType.Window)
        self.setStyleSheet("background:#0a0a0a;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # canvas
        self.canvas = QLabel()
        self.canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas.setStyleSheet("background:#0a0a0a;")
        self.canvas.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        root.addWidget(self.canvas, 1)

        # info bar
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info.setStyleSheet(
            "background:rgba(20,19,18,235); color:#cdccca; padding:10px 16px;"
            "border-top:1px solid #333;")
        root.addWidget(self.info)

        # nav / action bar
        bar = QHBoxLayout()
        bar.setContentsMargins(10, 6, 10, 8)
        for text, tip, fn in [
            ("‹ Prev", "Previous image (←)", self.prev),
            ("Next ›", "Next image (→)", self.next),
            ("Fit / 100%", "Toggle zoom (F)", self.toggle_fit),
            ("＋", "Zoom in", lambda: self.zoom_by(1.25)),
            ("－", "Zoom out", lambda: self.zoom_by(0.8)),
            ("Edit…", "Edit tags & metadata (E)", self.edit_current),
            ("Open folder", "Reveal the file on disk", self.reveal),
            ("Info", "Toggle info panel (I)", self.toggle_info),
            ("Close", "Close (Esc)", self.close),
        ]:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            b.setStyleSheet(
                "QPushButton{background:#1e1d1b;color:#cdccca;border:1px solid #3a3936;"
                "border-radius:6px;padding:5px 12px;}"
                "QPushButton:hover{border-color:#01696f;}")
            bar.addWidget(b)
        bar.addStretch()
        self.counter = QLabel()
        self.counter.setStyleSheet("color:#8d8b88;padding-right:6px;")
        bar.addWidget(self.counter)
        wrap = QWidget()
        wrap.setLayout(bar)
        wrap.setStyleSheet("background:#141312;")
        root.addWidget(wrap)

        for keys, fn in [
            ((Qt.Key.Key_Right, Qt.Key.Key_D), self.next),
            ((Qt.Key.Key_Left, Qt.Key.Key_A), self.prev),
        ]:
            for k in keys:
                QShortcut(QKeySequence(k), self, fn)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)
        QShortcut(QKeySequence(Qt.Key.Key_F), self, self.toggle_fit)
        QShortcut(QKeySequence(Qt.Key.Key_I), self, self.toggle_info)
        QShortcut(QKeySequence(Qt.Key.Key_E), self, self.edit_current)
        QShortcut(QKeySequence(Qt.Key.Key_Plus), self, lambda: self.zoom_by(1.25))
        QShortcut(QKeySequence(Qt.Key.Key_Equal), self, lambda: self.zoom_by(1.25))
        QShortcut(QKeySequence(Qt.Key.Key_Minus), self, lambda: self.zoom_by(0.8))

        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(int(screen.width() * 0.92), int(screen.height() * 0.92))
        self.load_current()

    # -- data --------------------------------------------------------------
    def current_id(self):
        return self.entries[self.index][0]

    def load_current(self):
        img_id, filepath, _ = self.entries[self.index]
        self._pixmap = QPixmap(filepath)
        self.zoom = None
        self.pan = QPoint(0, 0)
        self.refresh_info()
        self.render_image()

    def refresh_info(self):
        row = get_image_row(self.conn, self.current_id())
        tags = get_tags_for_image(self.conn, self.current_id())
        if not row:
            self.info.setText("")
            return
        dims = f"{row[4]}×{row[5]}" if row[4] else "unknown size"
        rating = (row[10] if len(row) > 10 else None) or UNRATED
        if rating not in RATINGS:
            rating = UNRATED
        colour = RATING_COLORS[rating]
        bits = [f"<b>{dims}</b>", f"source: {row[3] or 'unknown'}",
                f"<span style='color:{colour}'>{RATING_LABELS[rating].lower()}</span>"]
        if row[7]:
            bits.append(f"guidance: {row[7]}")
        if row[8]:
            bits.append(f"seed: {row[8]}")
        line1 = "  ·  ".join(bits)
        line2 = "tags: " + (", ".join(tags) if tags else "(untagged)")
        line3 = f"<br><span style='color:#9a9895'>{row[6]}</span>" if row[6] else ""
        self.info.setText(f"{line1}<br>{line2}{line3}")
        self.info.setVisible(self.show_info)
        self.counter.setText(f"{self.index + 1} / {len(self.entries)}")

    # -- rendering ---------------------------------------------------------
    def render_image(self):
        if self._pixmap.isNull():
            self.canvas.setText("Could not load this image.")
            self.canvas.setStyleSheet("color:#8d8b88;background:#0a0a0a;")
            return
        area = self.canvas.size()
        if self.zoom is None:
            pm = self._pixmap.scaled(
                area, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        else:
            w = max(1, int(self._pixmap.width() * self.zoom))
            h = max(1, int(self._pixmap.height() * self.zoom))
            pm = self._pixmap.scaled(
                w, h, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            if w > area.width() or h > area.height():
                # crop the visible region so panning works without a scroll area
                cw, ch = min(w, area.width()), min(h, area.height())
                max_x, max_y = max(0, w - cw), max(0, h - ch)
                x = min(max(0, (w - cw) // 2 - self.pan.x()), max_x)
                y = min(max(0, (h - ch) // 2 - self.pan.y()), max_y)
                pm = pm.copy(x, y, cw, ch)
        self.canvas.setPixmap(pm)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.render_image()

    # -- actions -----------------------------------------------------------
    def next(self):
        if self.index < len(self.entries) - 1:
            self.index += 1
            self.load_current()

    def prev(self):
        if self.index > 0:
            self.index -= 1
            self.load_current()

    def toggle_fit(self):
        self.zoom = None if self.zoom is not None else 1.0
        self.pan = QPoint(0, 0)
        self.render_image()

    def zoom_by(self, factor):
        base = self.zoom if self.zoom is not None else self._fit_scale()
        self.zoom = max(0.05, min(12.0, base * factor))
        self.render_image()

    def _fit_scale(self):
        if self._pixmap.isNull():
            return 1.0
        area = self.canvas.size()
        return min(area.width() / self._pixmap.width(),
                   area.height() / self._pixmap.height())

    def toggle_info(self):
        self.show_info = not self.show_info
        self.info.setVisible(self.show_info)
        self.render_image()

    def edit_current(self):
        dlg = EditDialog(self.conn, [self.current_id()], self)
        dlg.exec()
        self.refresh_info()
        p = self.parent()
        if hasattr(p, "_refresh_grid"):
            p._refresh_grid()

    def reveal(self):
        path = Path(self.entries[self.index][1])
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except Exception:
            pass

    # -- mouse -------------------------------------------------------------
    def wheelEvent(self, e):
        self.zoom_by(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self._drag_origin is not None and self.zoom is not None:
            delta = e.position().toPoint() - self._drag_origin
            self._drag_origin = e.position().toPoint()
            self.pan += delta
            self.render_image()

    def mouseReleaseEvent(self, e):
        self._drag_origin = None

    def mouseDoubleClickEvent(self, e):
        self.toggle_fit()


# ─── Main window ────────────────────────────────────────────────────────────

class GalleryWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Jawless Gallery")
        self.resize(1240, 800)
        self.conn = get_db()
        self.mode = "1x1"
        self.entries = []
        self.view_sizes = view_modes()
        self.jobs = None                 # imagetools.JobQueue, made on demand
        self._auto_seen = set()          # ids already handed to the auto queue

        # "Drop missing files on open" (settings → Gallery)
        if setting("gallery_prune_on_open", False):
            try:
                prune_missing(self.conn)
            except sqlite3.Error:
                pass

        self._build_ui()

        # "Auto-import new images" (settings → Gallery); the ↻ Rescan button
        # still works when this is off
        if setting("gallery_auto_import", True):
            self._rescan()
        else:
            self._reload_tag_list()
            self._refresh_grid()
            self.status.showMessage(
                "Auto-import is off — press ↻ Rescan to look for new images", 6000)

    # -- ui ---------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ──
        header = QFrame()
        header.setFixedHeight(56)
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(8)

        title = QLabel("🖼 Gallery")
        title.setStyleSheet("font-size:16px; font-weight:700;")
        h.addWidget(title)
        h.addStretch()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tags / prompt / app / seed…")
        self.search.setFixedWidth(260)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refresh_grid)
        h.addWidget(self.search)

        res_label = QLabel("Res")
        res_label.setStyleSheet("color:#8d8b88;")
        h.addWidget(res_label)
        self.res_combo = QComboBox()
        for label, w, ht in RESOLUTIONS:
            self.res_combo.addItem(label, (w, ht))
        self.res_combo.setFixedWidth(110)
        # default from settings, applied before connecting so it can't fire a
        # refresh while the grid is still being built
        self.res_combo.setCurrentIndex(self._default_res_index())
        self.res_combo.currentIndexChanged.connect(self._refresh_grid)
        h.addWidget(self.res_combo)

        self.mode_buttons = {}
        for label, key in [("1×1", "1x1"), ("1.5×1", "1.5x1"), ("1×1.5", "1x1.5")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == self.mode)
            btn.clicked.connect(lambda _, k=key: self._set_mode(k))
            self.mode_buttons[key] = btn
            h.addWidget(btn)

        tagmgr_btn = QPushButton("🏷 Tags…")
        tagmgr_btn.setToolTip("Rename or delete tags across the library")
        tagmgr_btn.clicked.connect(self._open_tag_manager)
        h.addWidget(tagmgr_btn)

        self.ai_btn = QToolButton()
        self.ai_btn.setText("🤖 AI…")
        self.ai_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.ai_btn.setStyleSheet(
            "QToolButton{background:#22211f;border:1px solid #393836;"
            "border-radius:6px;padding:5px 10px;}"
            "QToolButton:hover{border-color:#01696f;}"
            "QToolButton::menu-indicator{image:none;}")
        self.ai_btn.clicked.connect(self.ai_btn.showMenu)
        h.addWidget(self.ai_btn)
        self._refresh_ai_button()

        rescan_btn = QPushButton("↻ Rescan")
        rescan_btn.clicked.connect(self._rescan)
        h.addWidget(rescan_btn)

        layout.addWidget(header)

        # ── Body: tag sidebar | grid ──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        side = QWidget()
        s = QVBoxLayout(side)
        s.setContentsMargins(12, 10, 8, 12)
        s.setSpacing(8)

        side_head = QHBoxLayout()
        lbl = QLabel("Filter by tags")
        lbl.setStyleSheet("font-weight:700;")
        side_head.addWidget(lbl)
        side_head.addStretch()
        clear = QToolButton()
        clear.setText("clear")
        clear.setToolTip("Clear all tag filters")
        clear.setStyleSheet("QToolButton{border:none;color:#8d8b88;}"
                            "QToolButton:hover{color:#01a3ac;}")
        clear.clicked.connect(self._clear_tag_filter)
        side_head.addWidget(clear)
        s.addLayout(side_head)

        self.match_combo = QComboBox()
        self.match_combo.addItem("Match ALL selected tags (AND)", True)
        self.match_combo.addItem("Match ANY selected tag (OR)", False)
        self.match_combo.setCurrentIndex(
            0 if setting("gallery_tag_match", "all") == "all" else 1)
        self.match_combo.currentIndexChanged.connect(self._refresh_grid)
        s.addWidget(self.match_combo)

        self.tag_search = QLineEdit()
        self.tag_search.setPlaceholderText("Find a tag…")
        self.tag_search.setClearButtonEnabled(True)
        self.tag_search.textChanged.connect(self._filter_tag_list)
        s.addWidget(self.tag_search)

        self.tag_list = QListWidget()
        self.tag_list.itemChanged.connect(self._refresh_grid)
        s.addWidget(self.tag_list, 1)

        self.untagged_check = QCheckBox("Untagged only")
        self.untagged_check.stateChanged.connect(self._refresh_grid)
        s.addWidget(self.untagged_check)

        # ── Content rating ──
        # Not a tag: an image has exactly one rating, so this is its own group.
        # Nothing checked = show everything.
        rating_box = QGroupBox("Content rating")
        rb = QVBoxLayout(rating_box)
        rb.setContentsMargins(10, 6, 10, 8)
        rb.setSpacing(4)
        self.rating_checks = {}
        for key in RATING_FILTER_ORDER:
            cb = QCheckBox(RATING_LABELS[key])
            cb.setStyleSheet(f"QCheckBox{{color:{RATING_COLORS[key]};}}")
            cb.stateChanged.connect(self._refresh_grid)
            self.rating_checks[key] = cb
            rb.addWidget(cb)
        foot = QHBoxLayout()
        self.rating_hint = QLabel("all shown")
        self.rating_hint.setStyleSheet("color:#8d8b88;")
        foot.addWidget(self.rating_hint)
        foot.addStretch()
        rclear = QToolButton()
        rclear.setText("clear")
        rclear.setToolTip("Show every rating")
        rclear.setStyleSheet("QToolButton{border:none;color:#8d8b88;}"
                             "QToolButton:hover{color:#01a3ac;}")
        rclear.clicked.connect(self._clear_rating_filter)
        foot.addWidget(rclear)
        rb.addLayout(foot)
        s.addWidget(rating_box)

        splitter.addWidget(side)

        # Grid (QListWidget in IconMode gives free-flowing wrap grid)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setSpacing(8)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemDoubleClicked.connect(self._open_spotlight)
        self._apply_icon_size()
        splitter.addWidget(self.list)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 1000])
        layout.addWidget(splitter, 1)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # queue readout + cancel, only shown while the AI queue has work
        self.queue_label = QLabel("")
        self.queue_label.setStyleSheet("color:#7fc3cd;")
        self.queue_cancel = QToolButton()
        self.queue_cancel.setText("✕ cancel")
        self.queue_cancel.setToolTip("Drop everything still queued")
        self.queue_cancel.setStyleSheet("QToolButton{border:none;color:#e0806f;}")
        self.queue_cancel.clicked.connect(self._cancel_jobs)
        self.queue_label.hide()
        self.queue_cancel.hide()
        self.status.addPermanentWidget(self.queue_label)
        self.status.addPermanentWidget(self.queue_cancel)

        QShortcut(QKeySequence(Qt.Key.Key_Return), self.list, self._spotlight_selected)
        QShortcut(QKeySequence(Qt.Key.Key_F2), self.list, self._edit_selected)
        QShortcut(QKeySequence("Ctrl+F"), self, self.search.setFocus)

        self.setStyleSheet("""
            QMainWindow, QWidget { background:#1c1b19; color:#cdccca;
                font-family:'Segoe UI',sans-serif; font-size:13px; }
            QLineEdit { background:#22211f; border:1px solid #393836;
                border-radius:6px; padding:5px 10px; }
            QPushButton { background:#22211f; border:1px solid #393836;
                border-radius:6px; padding:5px 12px; }
            QPushButton:hover { border-color:#01696f; }
            QPushButton:checked { background:#01696f; border-color:#01696f; color:#fff; }
            QComboBox { background:#22211f; border:1px solid #393836;
                border-radius:6px; padding:4px 8px; }
            QComboBox QAbstractItemView { background:#22211f; color:#cdccca;
                selection-background-color:#01696f; border:1px solid #393836; }
            QListWidget { background:#1c1b19; border:none; }
            QListWidget::item:selected { background:#01696f; border-radius:6px; }
            QSplitter::handle { background:#2a2927; width:1px; }
            QStatusBar { color:#8d8b88; }
            QCheckBox { color:#cdccca; }
            QScrollBar:vertical { background:#1c1b19; width:10px; }
            QScrollBar::handle:vertical { background:#3a3936; border-radius:5px; }
            QListWidget::indicator { width:14px; height:14px; border-radius:3px;
                border:1px solid #4d4b48; background:#22211f; }
            QListWidget::indicator:checked { background:#01a3ac; border-color:#01a3ac; }
            QCheckBox::indicator { width:14px; height:14px; border-radius:3px;
                border:1px solid #4d4b48; background:#22211f; }
            QCheckBox::indicator:checked { background:#01a3ac; border-color:#01a3ac; }
            QGroupBox { border:1px solid #2d2c2a; border-radius:6px;
                margin-top:10px; padding-top:6px; font-weight:700; }
            QGroupBox::title { subcontrol-origin:margin; left:8px;
                padding:0 4px; color:#8d8b88; }
        """)

    def _default_res_index(self) -> int:
        """Map the configured default resolution onto a combo row."""
        value = str(setting("gallery_default_res", "any"))
        if value == "other":
            return len(RESOLUTIONS) - 1
        if "x" in value:
            w, _, h = value.partition("x")
            for i, (_label, rw, rh) in enumerate(RESOLUTIONS):
                if (rw, rh) == (int(w), int(h)):
                    return i
        return 0

    def _apply_icon_size(self):
        w, h = self.view_sizes.get(self.mode, VIEW_MODES["1x1"])
        self.list.setIconSize(QSize(w, h))
        self.list.setGridSize(QSize(w + 16, h + 16))

    def changeEvent(self, e):
        super().changeEvent(e)
        # picking up settings.json when you come back from configurate.py
        if e.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._resync_settings()

    def _resync_settings(self):
        reload_settings()
        self._refresh_ai_button()          # models dir / provider may have moved
        sizes = view_modes()
        if sizes != self.view_sizes:
            self.view_sizes = sizes
            self._apply_icon_size()
            self._refresh_grid()
            self.status.showMessage("Thumbnail size updated from settings", 4000)

    def _set_mode(self, key):
        self.mode = key
        for k, btn in self.mode_buttons.items():
            btn.setChecked(k == key)
        self._apply_icon_size()
        self._refresh_grid()

    # -- tag sidebar -------------------------------------------------------
    def _selected_filter_tags(self):
        out = []
        for i in range(self.tag_list.count()):
            it = self.tag_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    def _reload_tag_list(self):
        checked = set(self._selected_filter_tags())
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        for name, count in tag_counts(self.conn):
            if count == 0 and name not in checked:
                continue          # unused tag — manage it from the Tags… dialog
            it = QListWidgetItem(f"{name}  ({count})")
            it.setData(Qt.ItemDataRole.UserRole, name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if name in checked
                             else Qt.CheckState.Unchecked)
            self.tag_list.addItem(it)
        self.tag_list.blockSignals(False)
        self._filter_tag_list()

    def _filter_tag_list(self):
        needle = self.tag_search.text().strip().lower()
        for i in range(self.tag_list.count()):
            it = self.tag_list.item(i)
            name = it.data(Qt.ItemDataRole.UserRole) or ""
            hide = bool(needle) and needle not in name.lower() \
                and it.checkState() != Qt.CheckState.Checked
            it.setHidden(hide)

    # -- rating sidebar ----------------------------------------------------
    def _selected_ratings(self):
        return [k for k, cb in self.rating_checks.items() if cb.isChecked()]

    def _clear_rating_filter(self):
        for cb in self.rating_checks.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._refresh_grid()

    def _refresh_rating_counts(self):
        """Put the library-wide count next to each rating checkbox."""
        try:
            counts = rating_counts(self.conn)
        except sqlite3.Error:
            return
        for key, cb in self.rating_checks.items():
            cb.setText(f"{RATING_LABELS[key]}  ({counts.get(key, 0)})")
        chosen = self._selected_ratings()
        self.rating_hint.setText(
            "all shown" if not chosen or len(chosen) == len(RATING_FILTER_ORDER)
            else f"{len(chosen)} of {len(RATING_FILTER_ORDER)}")

    def _clear_tag_filter(self):
        self.tag_list.blockSignals(True)
        for i in range(self.tag_list.count()):
            self.tag_list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self.tag_list.blockSignals(False)
        self._refresh_grid()

    def _open_tag_manager(self):
        TagManagerDialog(self.conn, self).exec()
        self._reload_tag_list()
        self._refresh_grid()

    # -- scanning / refresh ------------------------------------------------
    def _rescan(self):
        self.status.showMessage("Scanning for new images…")
        self.worker = ScanWorker()
        self.worker.finished_scan.connect(self._after_scan)
        self.worker.start()

    def _after_scan(self, new_ids=None):
        self._reload_tag_list()
        self._refresh_grid()
        if new_ids:
            self._auto_process(new_ids)

    def _current_resolution(self):
        w, h = self.res_combo.currentData()
        if w is None:
            return None
        if w == -1:
            return "other"
        return (w, h)

    def _refresh_grid(self):
        self.list.clear()
        tags = self._selected_filter_tags()
        rows = query_images(
            self.conn,
            text=self.search.text(),
            tags=tags,
            match_all=bool(self.match_combo.currentData()),
            resolution=self._current_resolution(),
            untagged_only=self.untagged_check.isChecked(),
            ratings=self._selected_ratings(),
        )
        self._refresh_rating_counts()
        self.entries = []
        for image_id, filepath, img_hash in rows:
            if not Path(filepath).exists():
                continue
            img_hash = img_hash or hashlib.sha1(filepath.encode()).hexdigest()
            tpath = get_or_make_thumb(filepath, img_hash)

            item = QListWidgetItem()
            item.setIcon(QIcon(tpath))
            item.setData(Qt.ItemDataRole.UserRole, (image_id, filepath))
            row = get_image_row(self.conn, image_id)
            itags = get_tags_for_image(self.conn, image_id)
            dims = f"{row[4]}×{row[5]}" if row and row[4] else "unknown size"
            rating = (row[10] if row and len(row) > 10 else None) or UNRATED
            if rating not in RATINGS:
                rating = UNRATED
            tip = [f"{dims}   ·   rating: {RATING_LABELS[rating].lower()}",
                   "tags: " + (", ".join(itags) if itags else "(untagged)")]
            if row and row[6]:
                tip.append("prompt: " + (row[6][:220] + ("…" if len(row[6]) > 220 else "")))
            if row and row[7]:
                tip.append(f"guidance: {row[7]}")
            if row and row[8]:
                tip.append(f"seed: {row[8]}")
            item.setToolTip("\n".join(tip))
            self.list.addItem(item)
            self.entries.append((image_id, filepath, img_hash))

        parts = [f"{len(self.entries)} image(s)"]
        if tags:
            joiner = " AND " if self.match_combo.currentData() else " OR "
            parts.append("tags: " + joiner.join(tags))
        if self._current_resolution() is not None:
            parts.append("res: " + self.res_combo.currentText())
        chosen = self._selected_ratings()
        if chosen and len(chosen) < len(RATING_FILTER_ORDER):
            parts.append("rating: " + ", ".join(chosen))
        self.status.showMessage("   ·   ".join(parts))

    # -- selection helpers -------------------------------------------------
    def _selected_ids(self):
        return [it.data(Qt.ItemDataRole.UserRole)[0] for it in self.list.selectedItems()]

    def _edit_selected(self):
        ids = self._selected_ids()
        if not ids:
            return
        dlg = EditDialog(self.conn, ids, self)
        dlg.exec()
        self._reload_tag_list()
        self._refresh_grid()

    def _spotlight_selected(self):
        items = self.list.selectedItems()
        if items:
            self._open_spotlight(items[0])

    def _open_spotlight(self, item=None):
        if item is None:
            return
        row = self.list.row(item)
        if not self.entries or row < 0:
            return
        viewer = SpotlightViewer(self.conn, list(self.entries), row, self)
        viewer.exec()
        self._reload_tag_list()
        self._refresh_grid()

    # -- context menu ------------------------------------------------------
    def _context_menu(self, pos):
        items = self.list.selectedItems()
        if not items:
            return
        ids = [it.data(Qt.ItemDataRole.UserRole)[0] for it in items]
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#22211f;color:#cdccca;border:1px solid #393836;}"
            "QMenu::item:selected{background:#01696f;}")

        if len(ids) == 1:
            menu.addAction("🔍 Spotlight view",
                           lambda: self._open_spotlight(items[0]))
        menu.addAction("✎ Edit tags & metadata…", self._edit_selected)
        menu.addAction("🏷 Quick add tag…", lambda: self._quick_add(ids))

        # quick-remove submenu with the tags actually present on the selection
        present = sorted({t for i in ids for t in get_tags_for_image(self.conn, i)})
        if present:
            sub = menu.addMenu("✕ Remove tag")
            for name in present:
                sub.addAction(name, lambda _=False, n=name: self._quick_remove(ids, n))

        # ── rating: one value per image, so a radio-ish submenu ──
        rsub = menu.addMenu("◈ Set rating")
        current = {get_rating(self.conn, i) for i in ids}
        for key in RATING_FILTER_ORDER:
            label = RATING_LABELS[key] + ("  ✓" if current == {key} else "")
            rsub.addAction(label,
                           lambda _=False, k=key: self._set_rating(ids, k))

        # ── optional local AI ──
        self._add_ai_menu(menu, ids)

        menu.addSeparator()
        menu.addAction("📁 Open containing folder",
                       lambda: self._reveal(items[0]))
        menu.addAction("⧉ Copy file path",
                       lambda: QGuiApplication.clipboard().setText(
                           "\n".join(it.data(Qt.ItemDataRole.UserRole)[1] for it in items)))
        menu.addSeparator()
        menu.addAction("🗑 Remove from gallery (keeps file)",
                       lambda: self._forget(ids))
        menu.addAction("🔥 Delete permanently from drive",
                       lambda: self._delete_permanently(ids))
        menu.exec(self.list.mapToGlobal(pos))

    def _set_rating(self, ids, key):
        for img_id in ids:
            set_rating(self.conn, img_id, key, source="manual")
        self._refresh_grid()
        word = RATING_LABELS[key].lower()
        self.status.showMessage(f"Rating set to {word} on {len(ids)} image(s)", 4000)

    # ─── Optional local AI (imagetools.py) ───────────────────────────────
    def _ai_state(self):
        """(runtime_ok, tagger_ready, upscaler_ready) — all False without the module."""
        if imagetools is None:
            return False, False, False
        try:
            return (imagetools.available(),
                    imagetools.tagger_ready(),
                    imagetools.upscaler_ready())
        except Exception:                       # noqa: BLE001
            return False, False, False

    def _refresh_ai_button(self):
        """Build the header AI menu, or hide the button when nothing can run."""
        if not hasattr(self, "ai_btn"):
            return
        runtime, tagger, upscaler = self._ai_state()
        if imagetools is None:
            self.ai_btn.hide()
            return
        self.ai_btn.show()
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#22211f;color:#cdccca;border:1px solid #393836;}"
            "QMenu::item:selected{background:#01696f;}"
            "QMenu::item:disabled{color:#6a6866;}")
        if not runtime:
            act = menu.addAction("onnxruntime not installed")
            act.setEnabled(False)
            menu.addAction("ⓘ How to enable AI tools…", self._ai_help)
            self.ai_btn.setText("🤖 AI off")
            self.ai_btn.setToolTip(imagetools.import_error()
                                   or "onnxruntime is not installed")
        else:
            self.ai_btn.setText("🤖 AI…")
            self.ai_btn.setToolTip(imagetools.status_text())
            if tagger:
                menu.addAction("🤖 Auto-tag everything unrated",
                               self._tag_all_unrated)
            else:
                menu.addAction("⬇ Download tagger model (~467 MB)…",
                               self._download_models)
            if not upscaler:
                act = menu.addAction("No upscale model found")
                act.setEnabled(False)
                menu.addAction("📁 Open models folder", self._open_models_dir)
            menu.addSeparator()
            if self.jobs is not None and self.jobs.pending():
                menu.addAction("✕ Cancel queued jobs", self._cancel_jobs)
            menu.addAction("ⓘ Status…", self._ai_help)
        self.ai_btn.setMenu(menu)

    def _add_ai_menu(self, menu, ids):
        """Per-image AI entries. Nothing is added when the module is absent."""
        if imagetools is None:
            return
        runtime, tagger, upscaler = self._ai_state()
        menu.addSeparator()
        if not runtime:
            act = menu.addAction("🤖 AI tools need onnxruntime")
            act.setEnabled(False)
            act.setToolTip(imagetools.import_error())
            return

        n = len(ids)
        many = f" ({n})" if n > 1 else ""
        if tagger:
            menu.addAction(f"🤖 Auto-tag + rate{many}",
                           lambda: self._queue_jobs(ids, tag=True))
        else:
            menu.addAction("⬇ Download tagger model (~467 MB)…",
                           self._download_models)
        if upscaler:
            sub = menu.addMenu(f"⬆ Upscale{many}")
            for factor in (2, 4):
                sub.addAction(f"×{factor}",
                              lambda _=False, f=factor:
                              self._queue_jobs(ids, upscale=f))
            if tagger:
                sub.addSeparator()
                sub.addAction("×2 + auto-tag",
                              lambda: self._queue_jobs(ids, tag=True, upscale=2))
                sub.addAction("×4 + auto-tag",
                              lambda: self._queue_jobs(ids, tag=True, upscale=4))
        else:
            act = menu.addAction("⬆ Upscale — no model in models/upscale")
            act.setEnabled(False)

        if any(get_auto_tags_for_image(self.conn, i) for i in ids):
            menu.addAction(f"♻ Clear AI-suggested tags{many}",
                           lambda: self._clear_auto(ids))

    def _ensure_queue(self):
        if self.jobs is None:
            self.jobs = imagetools.JobQueue(self)
            self.jobs.progress.connect(self._on_job_progress)
            self.jobs.counts.connect(self._on_job_counts)
            self.jobs.tagged.connect(self._on_tagged)
            self.jobs.upscaled.connect(self._on_upscaled)
            self.jobs.failed.connect(self._on_job_failed)
            self.jobs.idle.connect(self._on_jobs_idle)
        return self.jobs

    def _queue_jobs(self, ids, tag=False, upscale=None, quiet=False):
        runtime, tagger_ok, upscaler_ok = self._ai_state()
        if not runtime:
            return 0
        if tag and not tagger_ok:
            tag = False
        if upscale and not upscaler_ok:
            upscale = None
        if not tag and not upscale:
            return 0

        jobs = []
        for img_id in ids:
            row = get_image_row(self.conn, img_id)
            if not row or not Path(row[1]).exists():
                continue
            if tag:
                jobs.append({"kind": "tag", "image_id": img_id, "path": row[1]})
            if upscale:
                jobs.append({"kind": "upscale", "image_id": img_id,
                             "path": row[1], "scale": int(upscale)})
        if not jobs:
            return 0
        self._ensure_queue().submit(jobs)
        if not quiet:
            self.status.showMessage(f"Queued {len(jobs)} AI job(s)", 4000)
        self._refresh_ai_button()
        return len(jobs)

    def _auto_process(self, new_ids):
        """'Process new images automatically' (settings → Image tools).

        off | tag | upscale | both. Only ever touches rows the scan just
        inserted — your existing library is never reprocessed behind your back.
        """
        mode = str(setting("imagetools_auto", "off") or "off").lower()
        if mode == "off" or imagetools is None:
            return
        fresh = [i for i in new_ids if i not in self._auto_seen]
        if not fresh:
            return
        self._auto_seen.update(fresh)
        scale = int(setting("upscale_scale", 2) or 2)
        queued = self._queue_jobs(
            fresh,
            tag=mode in ("tag", "both"),
            upscale=scale if mode in ("upscale", "both") else None,
            quiet=True,
        )
        if queued:
            self.status.showMessage(
                f"{len(fresh)} new image(s) — queued {queued} AI job(s)", 5000)

    def _tag_all_unrated(self):
        rows = query_images(self.conn, ratings=[UNRATED])
        if not rows:
            self.status.showMessage("Nothing unrated — all images have a rating", 4000)
            return
        if QMessageBox.question(
            self, "Auto-tag unrated images",
            f"Run the tagger over {len(rows)} unrated image(s)?\n"
            "This happens in the background and can be cancelled."
        ) != QMessageBox.StandardButton.Yes:
            return
        self._queue_jobs([r[0] for r in rows], tag=True)

    def _clear_auto(self, ids):
        n = clear_auto_tags(self.conn, ids)
        self._reload_tag_list()
        self._refresh_grid()
        self.status.showMessage(f"Removed {n} AI-suggested tag(s)", 4000)

    def _cancel_jobs(self):
        if self.jobs is None:
            return
        dropped = self.jobs.cancel_pending()
        self.status.showMessage(f"Cancelled — dropped {dropped} queued job(s)", 4000)
        self.queue_label.hide()
        self.queue_cancel.hide()
        self._refresh_ai_button()

    # -- queue signal handlers (these run on the GUI thread, so DB writes are
    #    safe here; the worker itself never touches sqlite) -----------------
    def _on_job_progress(self, message):
        self.queue_label.setText(message)
        self.queue_label.setVisible(bool(message))

    def _on_job_counts(self, done, total):
        busy = total > 0 and done < total
        self.queue_cancel.setVisible(busy)
        if busy:
            self.queue_label.setVisible(True)

    def _on_tagged(self, image_id, result):
        try:
            for name in result.get("tags", []):
                add_tag_to_image(self.conn, image_id, name, auto=True, commit=False)
            scores = dict(result.get("general", [])) | dict(result.get("character", []))
            for name, score in scores.items():
                pretty = imagetools.pretty_tag(name)
                self.conn.execute(
                    "UPDATE image_tags SET score=? WHERE image_id=? AND tag_id="
                    "(SELECT id FROM tags WHERE name=?)",
                    (float(score), image_id, pretty.lower()))
            set_rating(self.conn, image_id, result.get("rating"), source="ai",
                       commit=False)
            self.conn.commit()
        except sqlite3.Error as exc:
            self.status.showMessage(f"Database error while saving tags: {exc}", 6000)
            return
        self._reload_tag_list()
        self._refresh_grid()

    def _on_upscaled(self, image_id, src, out):
        """Register the new file and inherit the source image's tags + rating."""
        try:
            w, h = read_size(Path(out))
            row = get_image_row(self.conn, image_id)
            cur = self.conn.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO images(filepath, hash, source_app, width,"
                " height, prompt, guidance_scale, seed, rating, rating_source,"
                " upscaled_from) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (str(out), file_hash(Path(out)),
                 row[3] if row else "unknown", w, h,
                 row[6] if row else None, row[7] if row else None,
                 row[8] if row else None, row[10] if row else None,
                 "inherited" if (row and row[10]) else None, image_id))
            new_id = cur.lastrowid
            if new_id:
                for name in get_tags_for_image(self.conn, image_id):
                    add_tag_to_image(self.conn, new_id, name, commit=False)
                add_tag_to_image(self.conn, new_id, "upscaled", commit=False)
            self.conn.commit()
        except (sqlite3.Error, OSError) as exc:
            self.status.showMessage(f"Could not register the upscale: {exc}", 6000)
            return
        self._reload_tag_list()
        self._refresh_grid()

    def _on_job_failed(self, image_id, kind, message):
        self.status.showMessage(f"⚠ {kind} failed: {message}", 9000)

    def _on_jobs_idle(self):
        self.queue_label.hide()
        self.queue_cancel.hide()
        self._refresh_ai_button()
        self.status.showMessage("AI queue finished", 4000)

    def _open_models_dir(self):
        target = imagetools.models_dir()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(target))          # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception:                          # noqa: BLE001
            self.status.showMessage(f"Models folder: {target}", 8000)

    def _ai_help(self):
        runtime, tagger, upscaler = self._ai_state()
        lines = [imagetools.status_text(), "",
                 f"Models folder:  {imagetools.models_dir()}", ""]
        if not runtime:
            lines += ["To enable the AI tools:", "",
                      "    pip install onnxruntime numpy", "",
                      "For GPU acceleration install one of these instead:",
                      "    onnxruntime-gpu        (NVIDIA, Windows/Linux)",
                      "    onnxruntime-directml   (any GPU, Windows)",
                      "    onnxruntime-silicon    (Apple, or plain "
                      "onnxruntime with CoreML)"]
        else:
            if not tagger:
                lines += ["Tagger: put model.onnx and selected_tags.csv from",
                          f"    {imagetools.TAGGER_REPO}",
                          f"  into {imagetools.tagger_dir()}",
                          "  or use ‘Download tagger model’ in the AI menu.", ""]
            if not upscaler:
                lines += ["Upscaler: drop any Real-ESRGAN style .onnx export",
                          f"  into {imagetools.upscale_dir()}",
                          "  (dynamic [1,3,H,W] input, e.g. the RRDBNet x4 exports)."]
        box = QMessageBox(self)
        box.setWindowTitle("AI image tools")
        box.setText("AI image tools")
        box.setInformativeText("\n".join(lines))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.exec()

    def _download_models(self):
        if imagetools is None or not imagetools.available():
            self._ai_help()
            return
        if QMessageBox.question(
            self, "Download tagger model",
            f"Download the tagger model from Hugging Face?\n\n"
            f"  repo:  {imagetools.TAGGER_REPO}\n"
            f"  size:  about 467 MB\n"
            f"  into:  {imagetools.tagger_dir()}"
        ) != QMessageBox.StandardButton.Yes:
            return

        dlg = QProgressDialog("Starting download…", "Cancel", 0, 100, self)
        dlg.setWindowTitle("Downloading tagger model")
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setMinimumWidth(420)
        self._dl = imagetools.Downloader(parent=self)

        def on_progress(message, pct):
            dlg.setLabelText(message)
            if pct < 0:
                dlg.setRange(0, 0)
            else:
                dlg.setRange(0, 100)
                dlg.setValue(pct)

        def on_done(ok, message):
            dlg.close()
            self.status.showMessage(message, 8000)
            self._refresh_ai_button()
            if not ok:
                QMessageBox.warning(self, "Download failed", message)

        self._dl.progress.connect(on_progress)
        self._dl.done.connect(on_done)
        dlg.canceled.connect(self._dl.stop)
        self._dl.start()
        dlg.exec()

    def closeEvent(self, e):
        if self.jobs is not None:
            self.jobs.shutdown()
        super().closeEvent(e)

    def _quick_add(self, ids):
        text, ok = QInputDialog.getText(
            self, "Add tag", f"Tag {len(ids)} image(s) with (comma separated):")
        if ok and text.strip():
            for tag in [t.strip() for t in text.split(",") if t.strip()]:
                for img_id in ids:
                    add_tag_to_image(self.conn, img_id, tag)
            self._reload_tag_list()
            self._refresh_grid()

    def _quick_remove(self, ids, name):
        for img_id in ids:
            remove_tag_from_image(self.conn, img_id, name)
        self._reload_tag_list()
        self._refresh_grid()

    def _forget(self, ids):
        if QMessageBox.question(
            self, "Remove from gallery",
            f"Remove {len(ids)} image(s) from the gallery database?\n"
            "The files themselves are not deleted (a rescan will re-add them)."
        ) != QMessageBox.StandardButton.Yes:
            return
        for img_id in ids:
            self.conn.execute("DELETE FROM image_tags WHERE image_id=?", (img_id,))
            self.conn.execute("DELETE FROM images WHERE id=?", (img_id,))
        self.conn.commit()
        self._reload_tag_list()
        self._refresh_grid()
        
    def _delete_permanently(self, ids):
        if QMessageBox.question(
            self, "Delete permanently",
            f"Are you absolutely sure you want to permanently delete {len(ids)} image(s) from your hard drive?\n"
            "This action cannot be undone!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return

        success_count = 0
        failed = []
        for img_id in ids:
            deleted, error = delete_image_and_record(self.conn, img_id)
            if deleted:
                success_count += 1
            else:
                failed.append(error)
        
        self.conn.commit()
        self._reload_tag_list()
        self._refresh_grid()
        if failed:
            QMessageBox.warning(
                self, "Some files were not deleted",
                f"Deleted {success_count} image(s). Kept {len(failed)} gallery "
                "record(s) because the files could not be removed:\n\n"
                + "\n".join(failed[:10])
            )
        else:
            self.status.showMessage(
                f"Permanently deleted {success_count} image(s) from disk and database.",
                5000)

    def _reveal(self, item):
        path = Path(item.data(Qt.ItemDataRole.UserRole)[1])
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Jawless Gallery")
    app.setOrganizationName("Jawless")
    win = GalleryWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
