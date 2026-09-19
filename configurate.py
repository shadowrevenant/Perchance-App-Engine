#!/usr/bin/env python3
"""
configurate.py — Settings & maintenance for Perchance App Engine

Two ways to use this file:

  1. As an app:      python configurate.py
  2. As a library:   from configurate import settings
                     settings.get("window_width")

Settings are stored as JSON in  <APP_ROOT>/settings.json  — this file never
rewrites config.py, so upgrading the engine can't clobber your preferences.
"""

import os
import re
import sys
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))
import config  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Settings store
# ──────────────────────────────────────────────────────────────────────────────

SETTINGS_PATH = config.APP_ROOT / "settings.json"

# key -> (default, kind, label, help)
# kind: bool | int | str | color | choice:<a|b|c> | dir | path
SCHEMA = {
    # ── Launcher ──────────────────────────────────────────────────────────
    "launcher_accent":      ("#01696f", "color", "Launcher accent",
                             "Header title, buttons and focus rings."),
    "default_app_accent":   ("#01696f", "color", "New app accent",
                             "Accent given to newly added generators."),
    "card_size":            (150, "int", "Card size (px)",
                             "Width of each generator card in the grid. 110–260."),
    "show_descriptions":    (True, "bool", "Show card descriptions",
                             "Hide for a denser grid."),
    "confirm_remove_app":   (True, "bool", "Confirm before removing an app",
                             "Removing a generator keeps its data either way."),
    "close_launcher_on_launch": (False, "bool", "Close launcher after launching",
                                "Generators run detached, so this is safe."),

    # ── Generator windows ─────────────────────────────────────────────────
    "window_width":         (1280, "int", "Default window width",
                             "Starting width for a generator window."),
    "window_height":        (860, "int", "Default window height",
                             "Starting height for a generator window."),
    "remember_geometry":    (True, "bool", "Remember window size and position",
                             "Stored per generator."),
    "start_maximized":      (False, "bool", "Start maximized", ""),
    "zoom_factor":          (100, "int", "Page zoom (%)",
                             "50–250. Applied to every generator page."),
    "enable_devtools":      (False, "bool", "Enable developer tools (F12)",
                             "Useful when writing overrides.js."),
    "block_popups":         (False, "bool", "Block pop-up windows",
                             "Off: pages that open a second tab/window get a "
                             "real separate window. On: they are blocked "
                             "(can break sign-in flows)."),
    "user_agent":           ("", "str", "User agent override",
                             "Leave blank to use the built-in Chromium UA."),

    # ── Downloads & data ──────────────────────────────────────────────────
    "download_dir":         ("", "dir", "Download folder",
                             "Blank = data/<slug>/files/ for each generator."),
    "ask_where_to_save":    (False, "bool", "Ask where to save each download", ""),
    "unique_filenames":     (True, "bool", "Never overwrite downloads",
                             "Appends (1), (2), … instead of replacing."),

    # ── Gallery ───────────────────────────────────────────────────────────
    "gallery_auto_import":  (True, "bool", "Auto-import new images",
                             "Scan data/ for new images when the gallery opens."),
    "gallery_thumb_size":   (220, "int", "Thumbnail size (px)", "120–420."),
    "gallery_tag_match":    ("all", "choice:all|any", "Default tag match",
                             "ALL = image must have every selected tag."),
    "gallery_default_res":  ("any", "choice:any|512x512|768x768|512x768|768x512|other",
                             "Default resolution filter", ""),
    "gallery_prune_on_open": (False, "bool", "Drop missing files on open",
                             "Removes gallery entries whose image no longer exists."),

    # ── Image tools (optional local AI — needs onnxruntime) ──────────────
    "imagetools_auto":      ("off", "choice:off|tag|upscale|both",
                             "Process new images automatically",
                             "What to run on images the gallery has just "
                             "imported. 'tag' is the safe default — upscaling "
                             "is slow on CPU. Existing images are never "
                             "reprocessed automatically."),
    "models_dir":           ("", "dir", "Models folder",
                             "Blank = assets/models/ under the program root. "
                             "Expects tagger/ and upscale/ inside it."),
    "onnx_provider":        ("auto", "choice:auto|cpu|cuda|directml|coreml|rocm",
                             "Inference device",
                             "auto picks the best provider your onnxruntime "
                             "build actually ships, falling back to CPU."),
    "onnx_threads":         (0, "int", "CPU threads",
                             "0 = let onnxruntime decide. Lower it to keep the "
                             "machine responsive while tagging."),
    "unload_models_when_idle": (True, "bool", "Free models when the queue empties",
                                "Releases about 1–2 GB of RAM between batches, "
                                "at the cost of reloading next time."),
    "tagger_threshold":     (35, "int", "Tag confidence (%)",
                             "General tags below this are discarded. 35 is the "
                             "WD tagger default; raise it for fewer, safer tags."),
    "tagger_char_threshold": (85, "int", "Character confidence (%)",
                              "Character tags are held to a higher bar than "
                              "general ones."),
    "tagger_max_tags":      (40, "int", "Maximum tags per image",
                             "0 = no limit. Character tags are always kept."),
    "upscale_scale":        ("2", "choice:2|4", "Upscale factor",
                             "Used by the menu default and by automatic "
                             "processing. ×4 on CPU is slow."),
    "upscale_model":        ("", "str", "Upscale model file",
                             "Blank = the first .onnx found in the upscale "
                             "folder."),
    "upscale_tile":         (512, "int", "Tile size (px)",
                             "Images are upscaled in tiles, so memory use "
                             "depends on this and not on the image size. "
                             "Lower it if you run out of memory."),
    "upscale_overlap":      (24, "int", "Tile overlap (px)",
                             "Extra context around each tile so seams don't "
                             "show. 16–32 is plenty."),
    "upscale_suffix":       ("_x{scale}", "str", "Upscale filename suffix",
                             "{scale} is replaced by the factor, so "
                             "art.png becomes art_x4.png. Originals are "
                             "never overwritten."),

    # ── Advanced ──────────────────────────────────────────────────────────
    "python_exe":           ("", "path", "Python executable",
                             "Blank = the interpreter running this app."),
    "disable_gpu":          (False, "bool", "Disable GPU acceleration",
                             "Try this if generator windows render black."),
    "perchance_compatibility_mode": (
        False, "bool", "Legacy Perchance compatibility mode",
        "Use only when Perchance or Cloudflare will not load normally. "
        "This weakens Chromium certificate and site-isolation protections "
        "for generator windows; restart open generators after changing it."),
    "chromium_flags":       ("", "str", "Extra Chromium flags",
                             "Space separated, e.g. --disable-features=Foo"),
    "prefer_lnk_shortcuts": (True, "bool", "Prefer real .lnk shortcuts (Windows)",
                             "Requires pywin32; falls back to a .bat file."),
}

GROUPS = [
    ("Launcher", ["launcher_accent", "default_app_accent", "card_size",
                  "show_descriptions", "confirm_remove_app",
                  "close_launcher_on_launch"]),
    ("Generators", ["window_width", "window_height", "remember_geometry",
                    "start_maximized", "zoom_factor", "enable_devtools",
                    "block_popups", "user_agent"]),
    ("Downloads", ["download_dir", "ask_where_to_save", "unique_filenames"]),
    ("Gallery", ["gallery_auto_import", "gallery_thumb_size",
                 "gallery_tag_match", "gallery_default_res",
                 "gallery_prune_on_open"]),
    ("Image tools", ["imagetools_auto", "models_dir", "onnx_provider",
                     "onnx_threads", "unload_models_when_idle",
                     "tagger_threshold", "tagger_char_threshold",
                     "tagger_max_tags", "upscale_scale", "upscale_model",
                     "upscale_tile", "upscale_overlap", "upscale_suffix"]),
    ("Advanced", ["python_exe", "disable_gpu", "perchance_compatibility_mode",
                  "chromium_flags", "prefer_lnk_shortcuts"]),
]

RANGES = {
    "card_size": (110, 260),
    "window_width": (480, 7680),
    "window_height": (360, 4320),
    "zoom_factor": (50, 250),
    "gallery_thumb_size": (120, 420),
    "tagger_threshold": (1, 99),
    "tagger_char_threshold": (1, 99),
    "tagger_max_tags": (0, 200),
    "upscale_tile": (64, 2048),
    "upscale_overlap": (0, 128),
    "onnx_threads": (0, 64),
}


class Settings:
    """Tiny JSON-backed settings store, safe to import from any script."""

    def __init__(self, path: Path = SETTINGS_PATH):
        self.path = Path(path)
        self._data = {}
        self.load()

    # -- io ---------------------------------------------------------------
    def load(self):
        data = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, ValueError):
                data = {}          # corrupt file -> fall back to defaults
        self._data = {k: v[0] for k, v in SCHEMA.items()}
        for k, v in data.items():
            if k in SCHEMA:
                self._data[k] = self._coerce(k, v)
        return self._data

    def save(self):
        """Atomic write so a crash mid-save can't truncate the file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(self.path)

    # -- access -----------------------------------------------------------
    def get(self, key, fallback=None):
        if key in self._data:
            return self._data[key]
        if key in SCHEMA:
            return SCHEMA[key][0]
        return fallback

    def set(self, key, value):
        if key in SCHEMA:
            self._data[key] = self._coerce(key, value)

    def reset(self, key=None):
        if key is None:
            self._data = {k: v[0] for k, v in SCHEMA.items()}
        elif key in SCHEMA:
            self._data[key] = SCHEMA[key][0]

    def as_dict(self):
        return dict(self._data)

    def _coerce(self, key, value):
        default, kind = SCHEMA[key][0], SCHEMA[key][1]
        try:
            if kind == "bool":
                if isinstance(value, str):
                    return value.strip().lower() in ("1", "true", "yes", "on")
                return bool(value)
            if kind == "int":
                num = int(value)
                lo, hi = RANGES.get(key, (None, None))
                if lo is not None:
                    num = max(lo, min(hi, num))
                return num
            if kind == "color":
                text = str(value).strip()
                return text if re.fullmatch(r"#[0-9a-fA-F]{6}", text) else default
            if kind.startswith("choice:"):
                options = kind.split(":", 1)[1].split("|")
                return str(value) if str(value) in options else default
            return str(value)
        except (TypeError, ValueError):
            return default


settings = Settings()


# ── Convenience helpers other scripts can use ────────────────────────────────

def python_exe() -> str:
    """Interpreter to launch child processes with."""
    configured = settings.get("python_exe")
    if configured and Path(configured).exists():
        return configured
    return sys.executable


def chromium_flags() -> str:
    flags = [settings.get("chromium_flags", "")]
    if settings.get("disable_gpu"):
        flags.append("--disable-gpu --disable-software-rasterizer")
    if settings.get("perchance_compatibility_mode"):
        flags.append(
            "--disable-blink-features=AutomationControlled,TrustedDOMTypes "
            "--disable-features=IsolateOrigins,site-per-process "
            "--enable-features=NetworkService,NetworkServiceInProcess "
            "--allow-running-insecure-content "
            "--ignore-certificate-errors --ignore-ssl-errors"
        )
    return " ".join(f for f in flags if f).strip()


def resolution_filter():
    """Gallery default resolution as None (any) | 'other' | (w, h)."""
    value = settings.get("gallery_default_res", "any")
    if value in ("any", "other"):
        return None if value == "any" else "other"
    w, _, h = value.partition("x")
    return (int(w), int(h))


def open_in_file_manager(path: Path):
    path = Path(path)
    try:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(path))            # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception:
        return False


def dir_size(path: Path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


# ──────────────────────────────────────────────────────────────────────────────
# GUI
# ──────────────────────────────────────────────────────────────────────────────

def _run_gui():
    from PyQt6.QtCore import Qt, QThread, pyqtSignal
    from PyQt6.QtGui import QIcon, QColor, QKeySequence, QShortcut, QFont
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QFrame, QLabel, QVBoxLayout,
        QHBoxLayout, QFormLayout, QTabWidget, QLineEdit, QCheckBox, QSpinBox,
        QComboBox, QPushButton, QColorDialog, QFileDialog, QMessageBox,
        QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
    )

    THEME = """
        QMainWindow, QWidget { background:#1c1b19; color:#cdccca;
            font-family:'Segoe UI','Inter',sans-serif; font-size:13px; }
        #header { background:#171614; }
        #title { font-size:17px; font-weight:700; color:#4f98a3;
            letter-spacing:0.5px; }
        #subtitle { color:#8b8a88; font-size:11px; }
        QTabWidget::pane { border:none; background:#1c1b19; }
        QTabBar::tab { background:#201f1d; color:#9a9896; padding:8px 16px;
            border:1px solid #2d2c2a; border-bottom:none;
            border-top-left-radius:8px; border-top-right-radius:8px;
            margin-right:3px; }
        QTabBar::tab:selected { background:#26312f; color:#7fc3cd;
            border-color:#3c5c5f; }
        QTabBar::tab:hover:!selected { background:#2a2927; color:#cdccca; }
        QLineEdit, QSpinBox, QComboBox { background:#22211f;
            border:1px solid #393836; border-radius:6px; padding:5px 8px;
            color:#cdccca; selection-background-color:#01696f; }
        QComboBox::drop-down { border:none; width:20px; }
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color:#4f98a3; }
        QLineEdit:disabled { color:#6b6a68; }
        QComboBox QAbstractItemView { background:#22211f; color:#cdccca;
            border:1px solid #393836; selection-background-color:#01696f; }
        QCheckBox { spacing:8px; }
        QCheckBox::indicator { width:16px; height:16px; border-radius:4px;
            border:1px solid #5a5957; background:#22211f; }
        QCheckBox::indicator:checked { background:#01696f; border-color:#4f98a3; }
        QCheckBox::indicator:hover { border-color:#4f98a3; }
        #primaryBtn { background:#01696f; border:none; border-radius:7px;
            padding:7px 18px; color:#f9f8f5; font-weight:600; }
        #primaryBtn:hover { background:#0c4e54; }
        #primaryBtn:disabled { background:#2a2927; color:#6b6a68; }
        QPushButton { background:#22211f; border:1px solid #393836;
            border-radius:7px; padding:6px 12px; color:#cdccca; }
        QPushButton:hover { background:#2d2c2a; border-color:#5a5957; }
        #dangerBtn { color:#e0806f; border-color:#4a2f2a; }
        #dangerBtn:hover { background:#33211d; border-color:#7d453a; }
        #helpLabel { color:#7c7b79; font-size:11px; }
        #sectionNote { color:#8b8a88; font-size:11px; }
        QScrollArea { border:none; background:#1c1b19; }
        QTableWidget { background:#201f1d; border:1px solid #2d2c2a;
            border-radius:8px; gridline-color:#2d2c2a; }
        QTableWidget::item:selected { background:#26312f; color:#cdccca; }
        QHeaderView::section { background:#22211f; color:#9a9896; border:none;
            border-bottom:1px solid #2d2c2a; padding:6px; }
        #statusBar { background:#171614; color:#8b8a88; font-size:11px; }
        #pathValue { color:#9a9896; font-family:Consolas,monospace;
            font-size:11px; }
        #dirtyDot { color:#e0a06f; font-size:11px; }
    """

    CHOICE_LABELS = {
        "all": "ALL — must have every tag",
        "any": "ANY — may have any tag",
        "any_res": "Any",
        "other": "Other / unlisted",
        # per-setting labels win over the bare option name, so short options
        # like "2" or "auto" can read differently in different places
        "imagetools_auto:off": "Off — do nothing",
        "imagetools_auto:tag": "Tag + rate only (recommended)",
        "imagetools_auto:upscale": "Upscale only",
        "imagetools_auto:both": "Tag, rate and upscale (slow)",
        "onnx_provider:auto": "Auto — best available",
        "onnx_provider:cpu": "CPU only",
        "onnx_provider:cuda": "CUDA (NVIDIA)",
        "onnx_provider:directml": "DirectML (any GPU, Windows)",
        "onnx_provider:coreml": "Core ML (Apple)",
        "onnx_provider:rocm": "ROCm (AMD, Linux)",
        "upscale_scale:2": "×2 — double",
        "upscale_scale:4": "×4 — quadruple (slow)",
    }

    class SizeScanner(QThread):
        """Directory sizes off the UI thread — data/ can be very large."""
        done = pyqtSignal(list, int)

        def run(self):
            rows, total = [], 0
            for slug in config.list_generators():
                base = config.DATA_DIR / slug
                cache = dir_size(base / "cache")
                store = dir_size(base / "storage")
                files = dir_size(base / "files")
                other = max(0, dir_size(base) - cache - store - files)
                rows.append((slug, cache, store, files, other))
                total += cache + store + files + other
            gallery = config.DATA_DIR / "_gallery"
            if gallery.exists():
                rows.append(("_gallery", dir_size(gallery / "thumbs"), 0, 0,
                             max(0, dir_size(gallery)
                                 - dir_size(gallery / "thumbs"))))
                total += dir_size(gallery)
            self.done.emit(rows, total)

    class ColorRow(QWidget):
        def __init__(self, value, on_change):
            super().__init__()
            self._value = value
            self._on_change = on_change
            row = QHBoxLayout(self)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            self.swatch = QPushButton("Pick…")
            self.swatch.setFixedWidth(78)
            self.swatch.clicked.connect(self._pick)
            self.field = QLineEdit(value)
            self.field.setFixedWidth(92)
            self.field.setPlaceholderText("#rrggbb")
            self.field.textEdited.connect(self._typed)
            row.addWidget(self.swatch)
            row.addWidget(self.field)
            row.addStretch()
            self._paint()

        def _pick(self):
            c = QColorDialog.getColor(QColor(self._value), self, "Accent color")
            if c.isValid():
                self._value = c.name()
                self.field.setText(self._value)
                self._paint()
                self._on_change(self._value)

        def _typed(self, text):
            c = QColor(text.strip())
            if c.isValid() and re.fullmatch(r"#[0-9a-fA-F]{6}", text.strip()):
                self._value = c.name()
                self._paint()
                self._on_change(self._value)

        def _paint(self):
            c = QColor(self._value)
            lum = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255
            fg = "#151413" if lum > 0.6 else "#ffffff"
            self.swatch.setStyleSheet(
                f"background:{self._value}; color:{fg}; border:none;"
                "border-radius:6px; padding:6px 10px; font-weight:600;")

        def value(self):
            return self._value

    class PathRow(QWidget):
        def __init__(self, value, on_change, pick_dir=True, placeholder=""):
            super().__init__()
            self._on_change = on_change
            self._pick_dir = pick_dir
            row = QHBoxLayout(self)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            self.field = QLineEdit(value)
            self.field.setPlaceholderText(placeholder)
            self.field.textEdited.connect(self._on_change)
            browse = QPushButton("Browse…")
            browse.setFixedWidth(84)
            browse.clicked.connect(self._browse)
            clear = QPushButton("Clear")
            clear.setFixedWidth(60)
            clear.clicked.connect(self._clear)
            row.addWidget(self.field, 1)
            row.addWidget(browse)
            row.addWidget(clear)

        def _browse(self):
            if self._pick_dir:
                path = QFileDialog.getExistingDirectory(self, "Choose folder")
            else:
                path, _ = QFileDialog.getOpenFileName(self, "Choose executable")
            if path:
                self.field.setText(path)
                self._on_change(path)

        def _clear(self):
            self.field.clear()
            self._on_change("")

        def value(self):
            return self.field.text().strip()

    class ConfigWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Jawless Perchance Engine — Settings")
            self.setMinimumSize(720, 560)
            self.resize(860, 680)
            icon = config.ASSETS_DIR / "launcher-icon.png"
            if icon.exists():
                self.setWindowIcon(QIcon(str(icon)))

            self.pending = settings.as_dict()
            self.widgets = {}
            self._dirty = False
            self._scanner = None

            self._build()
            self.setStyleSheet(THEME)
            self._mark_clean()

            QShortcut(QKeySequence("Ctrl+S"), self, self._save)
            QShortcut(QKeySequence("Ctrl+W"), self, self.close)
            QShortcut(QKeySequence("Esc"), self, self.close)

        # -- layout -------------------------------------------------------
        def _build(self):
            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)

            header = QFrame()
            header.setObjectName("header")
            header.setFixedHeight(58)
            # the QLabel rule matters: without it the labels paint the page
            # background and leave a lighter patch across the bar
            header.setStyleSheet(
                "QFrame#header { background:#171614; }"
                "QFrame#header QLabel { background:transparent; }")
            hl = QHBoxLayout(header)
            hl.setContentsMargins(20, 0, 16, 0)
            titles = QVBoxLayout()
            titles.setSpacing(0)
            title = QLabel("⚙ Jawless Engine Settings")
            title.setObjectName("title")
            self.subtitle = QLabel(str(SETTINGS_PATH))
            self.subtitle.setObjectName("subtitle")
            titles.addWidget(title)
            titles.addWidget(self.subtitle)
            hl.addLayout(titles)
            hl.addStretch()
            self.dirty_dot = QLabel("● unsaved changes")
            self.dirty_dot.setObjectName("dirtyDot")
            hl.addWidget(self.dirty_dot)
            root.addWidget(header)

            self.tabs = QTabWidget()
            root.addWidget(self.tabs, 1)
            for name, keys in GROUPS:
                self.tabs.addTab(self._settings_tab(keys), name)
            self.tabs.addTab(self._maintenance_tab(), "Maintenance")
            self.tabs.addTab(self._about_tab(), "About")

            bar = QFrame()
            bl = QHBoxLayout(bar)
            bl.setContentsMargins(16, 10, 16, 12)
            bl.setSpacing(8)
            reset_all = QPushButton("Reset all to defaults")
            reset_all.setObjectName("dangerBtn")
            reset_all.clicked.connect(self._reset_all)
            bl.addWidget(reset_all)
            bl.addStretch()
            revert = QPushButton("Revert")
            revert.clicked.connect(self._revert)
            self.save_btn = QPushButton("Save")
            self.save_btn.setObjectName("primaryBtn")
            self.save_btn.clicked.connect(self._save)
            bl.addWidget(revert)
            bl.addWidget(self.save_btn)
            root.addWidget(bar)

            self.status = QLabel("  Ready")
            self.status.setObjectName("statusBar")
            self.status.setFixedHeight(24)
            self.status.setStyleSheet(
                "background:#171614; color:#8b8a88; font-size:11px;")
            root.addWidget(self.status)

        def _settings_tab(self, keys):
            page = QWidget()
            outer = QVBoxLayout(page)
            outer.setContentsMargins(0, 0, 0, 0)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            inner = QWidget()
            form = QFormLayout(inner)
            form.setContentsMargins(22, 18, 22, 18)
            form.setSpacing(14)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                                   | Qt.AlignmentFlag.AlignVCenter)
            for key in keys:
                default, kind, label, helptext = SCHEMA[key]
                widget = self._editor(key, kind, self.pending.get(key, default))
                self.widgets[key] = widget
                cell = QWidget()
                cl = QVBoxLayout(cell)
                cl.setContentsMargins(0, 0, 0, 0)
                cl.setSpacing(2)
                cl.addWidget(widget)
                if helptext:
                    hint = QLabel(helptext)
                    hint.setObjectName("helpLabel")
                    hint.setWordWrap(True)
                    cl.addWidget(hint)
                form.addRow(f"{label}:", cell)
            scroll.setWidget(inner)
            outer.addWidget(scroll)
            return page

        def _editor(self, key, kind, value):
            if kind == "bool":
                box = QCheckBox()
                box.setChecked(bool(value))
                box.toggled.connect(lambda v, k=key: self._changed(k, v))
                return box
            if kind == "int":
                spin = QSpinBox()
                lo, hi = RANGES.get(key, (0, 100000))
                spin.setRange(lo, hi)
                spin.setValue(int(value))
                spin.setFixedWidth(110)
                # styled arrows render poorly on the dark theme; typing, the
                # scroll wheel and the arrow keys all still work
                spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
                spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
                spin.setToolTip(f"{RANGES.get(key, ('', ''))[0]}–"
                                f"{RANGES.get(key, ('', ''))[1]} "
                                "— scroll or use ↑/↓")
                spin.valueChanged.connect(lambda v, k=key: self._changed(k, v))
                return spin
            if kind == "color":
                return ColorRow(value, lambda v, k=key: self._changed(k, v))
            if kind in ("dir", "path"):
                if key == "download_dir":
                    placeholder = "data/<slug>/files/"
                elif key == "models_dir":
                    placeholder = str(config.ASSETS_DIR / "models")
                elif kind == "dir":
                    placeholder = str(config.APP_ROOT)
                else:
                    placeholder = sys.executable
                return PathRow(value, lambda v, k=key: self._changed(k, v),
                               pick_dir=(kind == "dir"), placeholder=placeholder)
            if kind.startswith("choice:"):
                combo = QComboBox()
                combo.setMinimumWidth(240)
                combo.setSizeAdjustPolicy(
                    QComboBox.SizeAdjustPolicy.AdjustToContents)
                for option in kind.split(":", 1)[1].split("|"):
                    fallback = CHOICE_LABELS.get(
                        "any_res" if (option == "any" and key.endswith("res"))
                        else option, option)
                    pretty = CHOICE_LABELS.get(f"{key}:{option}", fallback)
                    combo.addItem(pretty, option)
                index = max(0, combo.findData(value))
                combo.setCurrentIndex(index)
                combo.currentIndexChanged.connect(
                    lambda _i, k=key, c=combo: self._changed(k, c.currentData()))
                return combo
            field = QLineEdit(str(value))
            field.textEdited.connect(lambda v, k=key: self._changed(k, v))
            return field

        # -- maintenance --------------------------------------------------
        def _maintenance_tab(self):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(22, 18, 22, 18)
            layout.setSpacing(12)

            note = QLabel(
                "Clearing a cache is always safe — it only costs a re-download. "
                "Clearing storage signs you out of a generator and erases its "
                "localStorage. Your saved images in files/ are never touched here.")
            note.setObjectName("sectionNote")
            note.setWordWrap(True)
            layout.addWidget(note)

            self.table = QTableWidget(0, 5)
            self.table.setHorizontalHeaderLabels(
                ["Generator", "Cache", "Storage", "Files", "Other"])
            self.table.verticalHeader().setVisible(False)
            self.table.setSelectionBehavior(
                QTableWidget.SelectionBehavior.SelectRows)
            self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            head = self.table.horizontalHeader()
            head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for col in range(1, 5):
                head.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            layout.addWidget(self.table, 1)

            self.total_label = QLabel("Scanning…")
            self.total_label.setObjectName("sectionNote")
            layout.addWidget(self.total_label)

            row1 = QHBoxLayout()
            row1.setSpacing(8)
            rescan = QPushButton("↻  Rescan sizes")
            rescan.clicked.connect(self._scan_sizes)
            clear_sel = QPushButton("Clear cache (selected)")
            clear_sel.setObjectName("dangerBtn")
            clear_sel.clicked.connect(lambda: self._clear(["cache"], False))
            clear_all = QPushButton("Clear all caches")
            clear_all.setObjectName("dangerBtn")
            clear_all.clicked.connect(lambda: self._clear(["cache"], True))
            wipe_sel = QPushButton("Clear cache + storage (selected)")
            wipe_sel.setObjectName("dangerBtn")
            wipe_sel.clicked.connect(lambda: self._clear(["cache", "storage"], False))
            for widget in (rescan, clear_sel, clear_all, wipe_sel):
                row1.addWidget(widget)
            row1.addStretch()
            layout.addLayout(row1)

            row2 = QHBoxLayout()
            row2.setSpacing(8)
            for label, path in (
                ("📂 Program root", config.APP_ROOT),
                ("📂 data/", config.DATA_DIR),
                ("📂 gens/", config.GENS_DIR),
            ):
                btn = QPushButton(label)
                btn.clicked.connect(lambda _c=False, p=path: open_in_file_manager(p))
                row2.addWidget(btn)
            gal_btn = QPushButton("🗜  Compact gallery database")
            gal_btn.clicked.connect(self._vacuum_gallery)
            row2.addWidget(gal_btn)
            row2.addStretch()
            layout.addLayout(row2)

            self._scan_sizes()
            return page

        def _scan_sizes(self):
            if self._scanner and self._scanner.isRunning():
                return
            self.total_label.setText("Scanning…")
            self._scanner = SizeScanner()
            self._scanner.done.connect(self._sizes_ready)
            self._scanner.start()

        def _sizes_ready(self, rows, total):
            self.table.setRowCount(len(rows))
            for r, (slug, cache, store, files, other) in enumerate(rows):
                self.table.setItem(r, 0, QTableWidgetItem(slug))
                for c, val in enumerate((cache, store, files, other), start=1):
                    item = QTableWidgetItem(human_size(val))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                    self.table.setItem(r, c, item)
            self.total_label.setText(
                f"{len(rows)} folders · {human_size(total)} total in "
                f"{config.DATA_DIR}")

        def _selected_slugs(self):
            rows = {i.row() for i in self.table.selectedIndexes()}
            out = []
            for r in sorted(rows):
                item = self.table.item(r, 0)
                if item and item.text() != "_gallery":
                    out.append(item.text())
            return out

        def _clear(self, subdirs, everything):
            slugs = config.list_generators() if everything else self._selected_slugs()
            if not slugs:
                QMessageBox.information(
                    self, "Nothing selected",
                    "Select one or more rows in the table first.")
                return
            what = " and ".join(subdirs)
            if QMessageBox.question(
                    self, "Confirm",
                    f"Delete {what} for {len(slugs)} generator(s)?\n\n"
                    + ", ".join(slugs[:12])
                    + ("…" if len(slugs) > 12 else "")
                    + ("\n\nStorage includes logins and localStorage."
                       if "storage" in subdirs else "")
            ) != QMessageBox.StandardButton.Yes:
                return

            freed, failed = 0, []
            for slug in slugs:
                for sub in subdirs:
                    target = config.DATA_DIR / slug / sub
                    if not target.exists():
                        continue
                    freed += dir_size(target)
                    try:
                        shutil.rmtree(target)
                        target.mkdir(parents=True, exist_ok=True)
                    except OSError as exc:
                        failed.append(f"{slug}/{sub}: {exc}")
            self._scan_sizes()
            if failed:
                QMessageBox.warning(
                    self, "Partly cleared",
                    "Some folders are locked — close the generator windows and "
                    "try again.\n\n" + "\n".join(failed[:8]))
            else:
                self._status(f"Cleared {what} for {len(slugs)} generator(s) — "
                             f"{human_size(freed)} freed")

        def _vacuum_gallery(self):
            db = config.DATA_DIR / "_gallery" / "gallery.db"
            if not db.exists():
                QMessageBox.information(
                    self, "No gallery yet",
                    f"No database at\n{db}\n\nOpen the gallery once to create it.")
                return
            try:
                before = db.stat().st_size
                conn = sqlite3.connect(str(db))
                removed = 0
                try:
                    rows = conn.execute("SELECT id, path FROM images").fetchall()
                    missing = [i for i, p in rows if not Path(p).exists()]
                    for img_id in missing:
                        conn.execute("DELETE FROM image_tags WHERE image_id=?",
                                     (img_id,))
                        conn.execute("DELETE FROM images WHERE id=?", (img_id,))
                    removed = len(missing)
                    conn.commit()
                except sqlite3.Error:
                    pass                       # older schema: just vacuum
                conn.isolation_level = None
                conn.execute("VACUUM")
                conn.close()
                after = db.stat().st_size
                self._scan_sizes()
                QMessageBox.information(
                    self, "Gallery compacted",
                    f"Removed {removed} entries whose file was gone.\n"
                    f"{human_size(before)} → {human_size(after)}")
            except (sqlite3.Error, OSError) as exc:
                QMessageBox.critical(
                    self, "Could not compact",
                    f"{type(exc).__name__}: {exc}\n\n"
                    "Close the gallery window and try again.")

        # -- about --------------------------------------------------------
        def _about_tab(self):
            from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
            page = QWidget()
            layout = QFormLayout(page)
            layout.setContentsMargins(22, 18, 22, 18)
            layout.setSpacing(10)

            def value_label(text):
                lbl = QLabel(str(text))
                lbl.setObjectName("pathValue")
                lbl.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
                lbl.setWordWrap(True)
                lbl.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Preferred)
                return lbl

            try:
                import PyQt6.QtWebEngineCore as _wec
                web = getattr(_wec, "qWebEngineChromiumVersion", lambda: "?")()
            except Exception:
                web = "not installed"

            # optional local-AI module: report whether it can actually run
            try:
                import imagetools as _it
                ai_status = _it.status_text()
                # the label already says "AI image tools"
                for prefix in ("AI image tools — ", "AI image tools: "):
                    if ai_status.startswith(prefix):
                        ai_status = ai_status[len(prefix):]
                        break
            except Exception as _exc:                # noqa: BLE001
                ai_status = f"imagetools.py not loadable ({_exc})"

            rows = [
                ("Program root", config.APP_ROOT),
                ("Generators", config.GENS_DIR),
                ("Data", config.DATA_DIR),
                ("Assets", config.ASSETS_DIR),
                ("Global overrides", config.GLOBAL_JS),
                ("Runner", config.APP_RUNNER),
                ("Settings file", SETTINGS_PATH),
                ("Python", f"{sys.version.split()[0]}  ({sys.executable})"),
                ("Qt / PyQt", f"{QT_VERSION_STR} / {PYQT_VERSION_STR}"),
                ("Chromium", web),
                ("Platform", f"{sys.platform}"),
                ("Generators installed", len(config.list_generators())),
                ("AI image tools", ai_status),
            ]
            for label, value in rows:
                layout.addRow(f"{label}:", value_label(value))

            btn_row = QHBoxLayout()
            copy_btn = QPushButton("Copy diagnostics")
            copy_btn.clicked.connect(
                lambda: (QApplication.clipboard().setText(
                    "\n".join(f"{k}: {v}" for k, v in rows)),
                    self._status("Diagnostics copied to clipboard")))
            open_btn = QPushButton("Open settings.json")
            open_btn.clicked.connect(
                lambda: open_in_file_manager(SETTINGS_PATH.parent))
            btn_row.addWidget(copy_btn)
            btn_row.addWidget(open_btn)
            btn_row.addStretch()
            layout.addRow(btn_row)
            return page

        # -- state --------------------------------------------------------
        def _changed(self, key, value):
            self.pending[key] = value
            self._dirty = True
            self.dirty_dot.setVisible(True)
            self.save_btn.setEnabled(True)

        def _mark_clean(self):
            self._dirty = False
            self.dirty_dot.setVisible(False)
            self.save_btn.setEnabled(False)

        def _status(self, text):
            self.status.setText(f"  {text}")

        def _save(self):
            for key, value in self.pending.items():
                settings.set(key, value)
            try:
                settings.save()
            except OSError as exc:
                QMessageBox.critical(self, "Could not save",
                                     f"{SETTINGS_PATH}\n\n{exc}")
                return
            self.pending = settings.as_dict()
            self._reload_widgets()
            self._mark_clean()
            self._status(f"Saved to {SETTINGS_PATH}")

        def _revert(self):
            settings.load()
            self.pending = settings.as_dict()
            self._reload_widgets()
            self._mark_clean()
            self._status("Reverted to the last saved values")

        def _reset_all(self):
            if QMessageBox.question(
                    self, "Reset all settings",
                    "Restore every setting to its default value?\n\n"
                    "Nothing is written until you press Save."
            ) != QMessageBox.StandardButton.Yes:
                return
            self.pending = {k: v[0] for k, v in SCHEMA.items()}
            self._reload_widgets()
            self._dirty = True
            self.dirty_dot.setVisible(True)
            self.save_btn.setEnabled(True)
            self._status("Defaults loaded — press Save to apply")

        def _reload_widgets(self):
            """Push self.pending back into the editors without firing changes."""
            for key, widget in self.widgets.items():
                value = self.pending.get(key, SCHEMA[key][0])
                widget.blockSignals(True)
                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
                elif isinstance(widget, QSpinBox):
                    widget.setValue(int(value))
                elif isinstance(widget, QComboBox):
                    widget.setCurrentIndex(max(0, widget.findData(value)))
                elif isinstance(widget, ColorRow):
                    widget._value = value
                    widget.field.setText(str(value))
                    widget._paint()
                elif isinstance(widget, PathRow):
                    widget.field.setText(str(value))
                elif isinstance(widget, QLineEdit):
                    widget.setText(str(value))
                widget.blockSignals(False)

        def closeEvent(self, event):
            if self._dirty:
                answer = QMessageBox.question(
                    self, "Unsaved changes",
                    "Save your changes before closing?",
                    QMessageBox.StandardButton.Save
                    | QMessageBox.StandardButton.Discard
                    | QMessageBox.StandardButton.Cancel)
                if answer == QMessageBox.StandardButton.Cancel:
                    event.ignore()
                    return
                if answer == QMessageBox.StandardButton.Save:
                    self._save()
            if self._scanner and self._scanner.isRunning():
                self._scanner.quit()
                self._scanner.wait(1500)
            event.accept()

    app = QApplication(sys.argv)
    app.setApplicationName("Jawless Engine Settings")
    app.setOrganizationName("Jawless")
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    window = ConfigWindow()
    window.show()
    sys.exit(app.exec())


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _cli(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        print("Options:")
        print("  --print            show current settings as JSON")
        print("  --get KEY          print one value")
        print("  --set KEY VALUE    change one value and save")
        print("  --reset [KEY]      reset one setting, or all of them")
        print("  --path             print the settings file location")
        print("  --keys             list every setting with its default")
        return 0

    cmd = argv[0]
    if cmd == "--print":
        print(json.dumps(settings.as_dict(), indent=2, sort_keys=True))
    elif cmd == "--path":
        print(SETTINGS_PATH)
    elif cmd == "--keys":
        for key, (default, kind, label, _h) in SCHEMA.items():
            print(f"{key:24} {kind:22} default={default!r:12} {label}")
    elif cmd == "--get":
        if len(argv) < 2:
            print("usage: --get KEY", file=sys.stderr)
            return 2
        print(settings.get(argv[1]))
    elif cmd == "--set":
        if len(argv) < 3:
            print("usage: --set KEY VALUE", file=sys.stderr)
            return 2
        if argv[1] not in SCHEMA:
            print(f"unknown setting: {argv[1]}", file=sys.stderr)
            return 2
        settings.set(argv[1], argv[2])
        settings.save()
        print(f"{argv[1]} = {settings.get(argv[1])}")
    elif cmd == "--reset":
        settings.reset(argv[1] if len(argv) > 1 else None)
        settings.save()
        print("reset")
    else:
        print(f"unknown option: {cmd}  (try --help)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(_cli(sys.argv[1:]))
    _run_gui()
