#!/usr/bin/env python3
"""
imagetools.py — optional local AI helpers for the Perchance Gallery

Two features, both pure ONNX Runtime so the same code runs on Windows, Linux
and macOS with no platform-specific binaries:

  * Auto-tagging  — SmilingWolf/wd-swinv2-tagger-v3 (or any WD tagger ONNX
                    export that ships a selected_tags.csv alongside it).
                    Produces danbooru-style tags plus a content rating of
                    general / sensitive / questionable / explicit.
  * Upscaling     — any Real-ESRGAN style ONNX export with a dynamic
                    [1, 3, H, W] input and an [1, 3, H*s, W*s] output.
                    Tiled, so memory use is bounded by tile size and not by
                    image size.

Nothing here is required. gallery.py imports this defensively:

    try:
        import imagetools
    except Exception:
        imagetools = None

and every entry point below is safe to call even when onnxruntime is missing
or the model files have not been downloaded — see available(), tagger_ready()
and upscaler_ready().

Models are looked for in:

    <APP_ROOT>/assets/models/tagger/model.onnx
    <APP_ROOT>/assets/models/tagger/selected_tags.csv
    <APP_ROOT>/assets/models/upscale/*.onnx

(or the folder set by the models_dir setting). Download them yourself, or use
the built-in downloader — nothing is fetched without an explicit click.

No PIL dependency: image IO goes through Qt's QImage, which the gallery
already has loaded.
"""

from __future__ import annotations

import csv
import os
import sys
import time
import queue
import traceback
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QImage, QPainter, QColor

# program root (where config.py lives), plus its parent
_HERE = Path(__file__).parent.resolve()
for _p in (_HERE, _HERE.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import config  # noqa: E402
from core_utils import verify_file  # noqa: E402

# ─── Settings bridge (same defensive pattern as the other scripts) ──────────
try:
    from configurate import settings as _settings

    def setting(key, default=None):
        value = _settings.get(key, default)
        return default if value is None else value
except Exception:                                    # noqa: BLE001
    def setting(key, default=None):
        return default


# ─── Optional heavy imports ────────────────────────────────────────────────
_ORT = None
_NP = None
_IMPORT_ERROR = ""

try:
    import numpy as _np_mod
    _NP = _np_mod
except Exception as exc:                             # noqa: BLE001
    _IMPORT_ERROR = f"numpy is not installed ({exc})"

try:
    import onnxruntime as _ort_mod
    _ORT = _ort_mod
except Exception as exc:                             # noqa: BLE001
    if not _IMPORT_ERROR:
        _IMPORT_ERROR = f"onnxruntime is not installed ({exc})"


# ─── Content ratings ───────────────────────────────────────────────────────
# The WD taggers emit exactly these four, as a mutually exclusive group. The
# gallery adds a fifth pseudo-value, "unrated", for images the tagger has not
# seen yet. These are deliberately NOT stored as tags — a rating is one value
# per image, so it lives in its own column with its own filter.
RATINGS = ["general", "sensitive", "questionable", "explicit"]
UNRATED = "unrated"
RATING_LABELS = {
    "general":      "General",
    "sensitive":    "Sensitive",
    "questionable": "Questionable",
    "explicit":     "Explicit",
    UNRATED:        "Unrated",
}
# some exports abbreviate
_RATING_ALIASES = {
    "safe": "general", "g": "general", "s": "sensitive",
    "q": "questionable", "e": "explicit", "nsfw": "explicit",
}

# selected_tags.csv category numbers
CAT_GENERAL = 0
CAT_CHARACTER = 4
CAT_RATING = 9


# ─── Model discovery ───────────────────────────────────────────────────────

DEFAULT_MODELS_SUBDIR = Path("assets") / "models"

TAGGER_REPO = "SmilingWolf/wd-swinv2-tagger-v3"
TAGGER_REVISION = "627aef95638667ddcaa3ac8ae625e88ea5b02f51"
TAGGER_DOWNLOADS = [
    # (destination filename, URL, exact size, SHA-256)
    ("model.onnx",
     f"https://huggingface.co/{TAGGER_REPO}/resolve/{TAGGER_REVISION}/model.onnx?download=true",
     467_460_978,
     "e6774bff34d43bd49f75a47db4ef217dce701c9847b546523eb85ff6dbba1db1"),
    ("selected_tags.csv",
     f"https://huggingface.co/{TAGGER_REPO}/resolve/{TAGGER_REVISION}/selected_tags.csv?download=true",
     308_468,
     "298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217"),
]


def available() -> bool:
    """True when onnxruntime + numpy imported, i.e. anything here can run."""
    return _ORT is not None and _NP is not None


def import_error() -> str:
    return _IMPORT_ERROR


def models_dir() -> Path:
    custom = str(setting("models_dir", "") or "").strip()
    base = Path(custom).expanduser() if custom else (config.APP_ROOT / DEFAULT_MODELS_SUBDIR)
    return base


def tagger_dir() -> Path:
    return models_dir() / "tagger"


def upscale_dir() -> Path:
    return models_dir() / "upscale"


def tagger_paths():
    d = tagger_dir()
    return d / "model.onnx", d / "selected_tags.csv"


def tagger_ready() -> bool:
    if not available():
        return False
    model, labels = tagger_paths()
    return model.is_file() and labels.is_file()


def upscaler_candidates():
    """Every .onnx sitting in the upscale folder, newest name order."""
    d = upscale_dir()
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir()
                   if p.is_file() and p.suffix.lower() == ".onnx"),
                  key=lambda p: p.name.lower())


def upscaler_path():
    """The configured upscale model, or the first one found."""
    custom = str(setting("upscale_model", "") or "").strip()
    if custom:
        p = Path(custom).expanduser()
        if p.is_file():
            return p
        p2 = upscale_dir() / custom
        if p2.is_file():
            return p2
    found = upscaler_candidates()
    return found[0] if found else None


def upscaler_ready() -> bool:
    return available() and upscaler_path() is not None


def status_text() -> str:
    """One human-readable line for the About / Maintenance tab."""
    if not available():
        return ("AI image tools are off — " + (_IMPORT_ERROR or "onnxruntime missing") +
                ".  Install with:  pip install onnxruntime numpy")
    bits = []
    bits.append("tagger: " + ("ready" if tagger_ready() else "model not downloaded"))
    up = upscaler_path()
    bits.append("upscaler: " + (up.name if up else "no .onnx in " + str(upscale_dir())))
    bits.append("providers: " + ", ".join(active_providers()))
    return "AI image tools — " + "   ·   ".join(bits)


# ─── Execution providers ───────────────────────────────────────────────────

_PROVIDER_NAMES = {
    "cuda":     "CUDAExecutionProvider",
    "directml": "DmlExecutionProvider",
    "coreml":   "CoreMLExecutionProvider",
    "rocm":     "ROCMExecutionProvider",
    "openvino": "OpenVINOExecutionProvider",
    "cpu":      "CPUExecutionProvider",
}
# what to try first on each OS when the setting says "auto"
_AUTO_ORDER = {
    "win32":  ["cuda", "directml", "cpu"],
    "darwin": ["coreml", "cpu"],
}
_AUTO_DEFAULT = ["cuda", "rocm", "cpu"]


def active_providers():
    """Providers to hand to InferenceSession, best first, CPU always last."""
    if not available():
        return []
    installed = set(_ORT.get_available_providers())
    choice = str(setting("onnx_provider", "auto") or "auto").lower()
    if choice != "auto" and choice in _PROVIDER_NAMES:
        order = [choice, "cpu"]
    else:
        order = _AUTO_ORDER.get(sys.platform, _AUTO_DEFAULT)
    out = []
    for key in order:
        name = _PROVIDER_NAMES.get(key)
        if name and name in installed and name not in out:
            out.append(name)
    if "CPUExecutionProvider" not in out:
        out.append("CPUExecutionProvider")
    return out


def _make_session(path: Path):
    opts = _ORT.SessionOptions()
    opts.log_severity_level = 3                     # warnings and below: quiet
    threads = int(setting("onnx_threads", 0) or 0)
    if threads > 0:
        opts.intra_op_num_threads = threads
    return _ORT.InferenceSession(str(path), sess_options=opts,
                                 providers=active_providers())


# ─── QImage <-> numpy ──────────────────────────────────────────────────────

def load_rgb(path) -> "object":
    """Read an image as a contiguous H×W×3 uint8 RGB array.

    Transparency is composited onto white, which is what the taggers were
    trained on — compositing onto black shifts predictions noticeably.
    """
    img = QImage(str(path))
    if img.isNull():
        raise ValueError(f"could not read image: {path}")
    if img.hasAlphaChannel():
        canvas = QImage(img.size(), QImage.Format.Format_RGB888)
        canvas.fill(QColor(255, 255, 255))
        painter = QPainter(canvas)
        painter.drawImage(0, 0, img)
        painter.end()
        img = canvas
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    stride = img.bytesPerLine()
    arr = _NP.frombuffer(bytes(ptr), dtype=_NP.uint8).reshape(h, stride)
    arr = arr[:, : w * 3].reshape(h, w, 3)
    return _NP.ascontiguousarray(arr)


def save_rgb(arr, path) -> None:
    """Write an H×W×3 uint8 RGB array out as PNG (or whatever the suffix says)."""
    arr = _NP.ascontiguousarray(arr.astype(_NP.uint8))
    h, w, _ = arr.shape
    img = QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(path)):
        raise OSError(f"could not write image: {path}")


def _resize_rgb(arr, size: int):
    """Square resize through QImage (smooth / bilinear)."""
    h, w, _ = arr.shape
    arr = _NP.ascontiguousarray(arr)
    img = QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
    img = img.scaled(size, size,
                     Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    stride = img.bytesPerLine()
    out = _NP.frombuffer(bytes(ptr), dtype=_NP.uint8).reshape(size, stride)
    return _NP.ascontiguousarray(out[:, : size * 3].reshape(size, size, 3))


def _pad_square_white(arr):
    h, w, _ = arr.shape
    side = max(h, w)
    if h == w:
        return arr
    canvas = _NP.full((side, side, 3), 255, dtype=_NP.uint8)
    top = (side - h) // 2
    left = (side - w) // 2
    canvas[top:top + h, left:left + w] = arr
    return canvas


# ─── Tagger ────────────────────────────────────────────────────────────────

class Tagger:
    """WD-style multi-label tagger. Construct once, reuse for many images."""

    def __init__(self, model_path=None, labels_path=None):
        if not available():
            raise RuntimeError(_IMPORT_ERROR or "onnxruntime unavailable")
        model, labels = tagger_paths()
        self.model_path = Path(model_path or model)
        self.labels_path = Path(labels_path or labels)
        if not self.model_path.is_file():
            raise FileNotFoundError(self.model_path)
        if not self.labels_path.is_file():
            raise FileNotFoundError(self.labels_path)

        self.session = _make_session(self.model_path)
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.output_name = self.session.get_outputs()[0].name
        # WD taggers are NHWC: [1, H, W, 3]. Fall back to 448 for dynamic dims.
        shape = list(inp.shape)
        dim = None
        if len(shape) == 4:
            for candidate in (shape[1], shape[2]):
                if isinstance(candidate, int) and candidate > 1:
                    dim = candidate
                    break
        self.size = int(dim or 448)
        self.nchw = len(shape) == 4 and shape[1] == 3
        self._load_labels()

    def _load_labels(self):
        self.names = []
        self.categories = []
        with open(self.labels_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fields = {(f or "").strip().lower() for f in (reader.fieldnames or [])}
            if "name" not in fields:
                raise ValueError("selected_tags.csv has no 'name' column")
            for row in reader:
                low = {(k or "").strip().lower(): v for k, v in row.items()}
                self.names.append((low.get("name") or "").strip())
                try:
                    self.categories.append(int(low.get("category", CAT_GENERAL)))
                except (TypeError, ValueError):
                    self.categories.append(CAT_GENERAL)
        self.rating_idx = [i for i, c in enumerate(self.categories) if c == CAT_RATING]
        self.general_idx = [i for i, c in enumerate(self.categories) if c == CAT_GENERAL]
        self.char_idx = [i for i, c in enumerate(self.categories) if c == CAT_CHARACTER]

    # -- preprocessing ----------------------------------------------------
    def _prepare(self, path):
        arr = load_rgb(path)
        arr = _pad_square_white(arr)
        arr = _resize_rgb(arr, self.size)
        arr = arr[:, :, ::-1]                       # RGB -> BGR
        arr = arr.astype(_NP.float32)               # 0-255, no mean/std
        if self.nchw:
            return _NP.ascontiguousarray(arr.transpose(2, 0, 1)[None, ...])
        return _NP.ascontiguousarray(arr[None, ...])

    # -- inference --------------------------------------------------------
    def tag(self, path, general_threshold=None, character_threshold=None,
            max_tags=None):
        """Return a dict:

            {"rating": "general", "rating_scores": {...},
             "general": [(tag, score), …], "character": [(tag, score), …],
             "tags": ["tag", …]}
        """
        gt = float(general_threshold if general_threshold is not None
                   else setting("tagger_threshold", 35)) / 100.0
        ct = float(character_threshold if character_threshold is not None
                   else setting("tagger_char_threshold", 85)) / 100.0
        cap = int(max_tags if max_tags is not None else setting("tagger_max_tags", 40))

        probs = self.session.run([self.output_name],
                                 {self.input_name: self._prepare(path)})[0]
        probs = _NP.asarray(probs).reshape(-1)
        if probs.size < len(self.names):
            raise ValueError("model output is smaller than the label list — "
                             "model.onnx and selected_tags.csv do not match")

        rating_scores = {}
        for i in self.rating_idx:
            name = self.names[i].strip().lower()
            name = _RATING_ALIASES.get(name, name)
            rating_scores[name] = float(probs[i])
        rating = max(rating_scores, key=rating_scores.get) if rating_scores else UNRATED
        if rating not in RATINGS:
            rating = UNRATED

        general = [(self.names[i], float(probs[i]))
                   for i in self.general_idx if probs[i] >= gt]
        character = [(self.names[i], float(probs[i]))
                     for i in self.char_idx if probs[i] >= ct]
        general.sort(key=lambda t: -t[1])
        character.sort(key=lambda t: -t[1])
        if cap > 0:
            general = general[:cap]

        return {
            "rating": rating,
            "rating_scores": rating_scores,
            "general": general,
            "character": character,
            # characters first — they are the more useful filter
            "tags": [pretty_tag(n) for n, _ in character] +
                    [pretty_tag(n) for n, _ in general],
        }


def pretty_tag(name: str) -> str:
    """danbooru 'long_hair' -> 'long hair'. Kaomoji-ish tags are left alone."""
    name = (name or "").strip()
    if not name:
        return name
    if any(ch in name for ch in "()^;:><"):          # >_<, ^_^, o_O …
        return name
    return name.replace("_", " ")


# ─── Upscaler ──────────────────────────────────────────────────────────────

class Upscaler:
    """Tiled ESRGAN-style upscaler.

    Each tile is run with `overlap` pixels of context on every side, and only
    the tile's interior is copied into the output, so seams get real context
    instead of being blended after the fact.
    """

    def __init__(self, model_path=None):
        if not available():
            raise RuntimeError(_IMPORT_ERROR or "onnxruntime unavailable")
        path = Path(model_path) if model_path else upscaler_path()
        if not path or not Path(path).is_file():
            raise FileNotFoundError(path or upscale_dir())
        self.model_path = Path(path)
        self.session = _make_session(self.model_path)
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        self.scale = self._probe_scale()

    def _probe_scale(self):
        """Run one tiny tile to learn the model's native scale factor."""
        override = int(setting("upscale_model_scale", 0) or 0)
        if override in (2, 3, 4, 8):
            return override
        probe = _NP.zeros((1, 3, 32, 32), dtype=_NP.float32)
        try:
            out = self.session.run([self.output_name], {self.input_name: probe})[0]
            factor = int(round(out.shape[-1] / 32))
            return factor if factor >= 1 else 4
        except Exception:                            # noqa: BLE001
            return 4

    def _run_tile(self, tile_rgb):
        x = tile_rgb.astype(_NP.float32).transpose(2, 0, 1)[None, ...] / 255.0
        y = self.session.run([self.output_name], {self.input_name: x})[0]
        y = _NP.asarray(y)[0]
        y = _NP.clip(y, 0.0, 1.0).transpose(1, 2, 0) * 255.0
        return y.astype(_NP.uint8)

    def upscale_array(self, arr, tile=None, overlap=None, progress=None,
                      cancelled=None):
        tile = int(tile or setting("upscale_tile", 512) or 512)
        tile = max(64, min(2048, tile))
        overlap = int(overlap if overlap is not None
                      else setting("upscale_overlap", 24) or 24)
        overlap = max(0, min(128, overlap))
        s = self.scale
        h, w, _ = arr.shape

        # small enough to do in one shot
        if h <= tile and w <= tile:
            if progress:
                progress(0, 1)
            out = self._run_tile(arr)
            if progress:
                progress(1, 1)
            return out

        out = _NP.zeros((h * s, w * s, 3), dtype=_NP.uint8)
        ys = list(range(0, h, tile))
        xs = list(range(0, w, tile))
        total = len(ys) * len(xs)
        done = 0
        for y0 in ys:
            for x0 in xs:
                if cancelled and cancelled():
                    raise Cancelled()
                y1 = min(h, y0 + tile)
                x1 = min(w, x0 + tile)
                # expand by the overlap for context, clamped to the image
                py0 = max(0, y0 - overlap)
                px0 = max(0, x0 - overlap)
                py1 = min(h, y1 + overlap)
                px1 = min(w, x1 + overlap)
                piece = self._run_tile(arr[py0:py1, px0:px1])
                # drop the context border back off, in output pixels
                top = (y0 - py0) * s
                left = (x0 - px0) * s
                out[y0 * s:y1 * s, x0 * s:x1 * s] = \
                    piece[top:top + (y1 - y0) * s, left:left + (x1 - x0) * s]
                done += 1
                if progress:
                    progress(done, total)
        return out

    def upscale(self, src, dst, target_scale=None, tile=None, overlap=None,
                progress=None, cancelled=None):
        """Upscale `src` to `dst`. If target_scale is smaller than the model's
        native scale (e.g. 2 with an x4 model) the result is downsampled to
        the requested size afterwards, which is how the reference
        implementations do it too."""
        arr = load_rgb(src)
        out = self.upscale_array(arr, tile=tile, overlap=overlap,
                                 progress=progress, cancelled=cancelled)
        want = int(target_scale or self.scale)
        if want and want != self.scale:
            h, w, _ = arr.shape
            out = _resize_exact(out, w * want, h * want)
        save_rgb(out, dst)
        return dst


def _resize_exact(arr, w, h):
    src = _NP.ascontiguousarray(arr)
    sh, sw, _ = src.shape
    img = QImage(src.data, sw, sh, sw * 3, QImage.Format.Format_RGB888).copy()
    img = img.scaled(int(w), int(h),
                     Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    stride = img.bytesPerLine()
    out = _NP.frombuffer(bytes(ptr), dtype=_NP.uint8).reshape(img.height(), stride)
    return _NP.ascontiguousarray(out[:, : img.width() * 3]
                                 .reshape(img.height(), img.width(), 3))


def upscale_output_path(src, scale, suffix=None):
    """Where an upscale lands: alongside the original as name_x4.png."""
    src = Path(src)
    pattern = str(suffix or setting("upscale_suffix", "_x{scale}") or "_x{scale}")
    tail = pattern.replace("{scale}", str(scale))
    out = src.with_name(f"{src.stem}{tail}.png")
    n = 2
    while out.exists():
        out = src.with_name(f"{src.stem}{tail} ({n}).png")
        n += 1
    return out


# ─── Job queue ─────────────────────────────────────────────────────────────

class Cancelled(Exception):
    pass


class JobQueue(QThread):
    """One background worker that chews through tag / upscale jobs in order.

    Deliberately does no database work — results come back over signals and
    the GUI thread writes them, so the sqlite connection stays single-threaded.

    Jobs are dicts: {"kind": "tag"|"upscale", "image_id": int, "path": str,
                     "scale": int|None}
    """

    progress = pyqtSignal(str)                 # human-readable status line
    counts = pyqtSignal(int, int)              # done, total (this batch)
    tagged = pyqtSignal(int, object)           # image_id, tagger result dict
    upscaled = pyqtSignal(int, str, str)       # image_id, src path, out path
    failed = pyqtSignal(int, str, str)         # image_id, kind, message
    idle = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._q = queue.Queue()
        self._stop = False
        self._cancel_batch = False
        self._tagger = None
        self._upscaler = None
        self._submitted = 0
        self._done = 0

    # -- public API (call from the GUI thread) -----------------------------
    def submit(self, jobs):
        jobs = [j for j in (jobs or []) if j]
        if not jobs:
            return 0
        self._cancel_batch = False
        for job in jobs:
            self._q.put(job)
            self._submitted += 1
        self.counts.emit(self._done, self._submitted)
        if not self.isRunning():
            self._stop = False
            self.start()
        return len(jobs)

    def pending(self):
        return max(0, self._submitted - self._done)

    def cancel_pending(self):
        """Drop everything not started yet and abort the running tile loop."""
        self._cancel_batch = True
        dropped = 0
        while True:
            try:
                self._q.get_nowait()
                dropped += 1
            except queue.Empty:
                break
        self._submitted = self._done
        self.counts.emit(self._done, self._submitted)
        return dropped

    def shutdown(self, wait_ms=4000):
        self.cancel_pending()
        self._stop = True
        self._q.put(None)
        self.wait(wait_ms)

    def _cancelled(self):
        return self._stop or self._cancel_batch

    # -- worker ------------------------------------------------------------
    def run(self):
        while not self._stop:
            try:
                job = self._q.get(timeout=0.4)
            except queue.Empty:
                if self._submitted and self._done >= self._submitted:
                    self._reset_batch()
                continue
            if job is None:
                break
            try:
                self._do(job)
            except Cancelled:
                self.progress.emit("Cancelled")
            except Exception as exc:                 # noqa: BLE001
                self.failed.emit(int(job.get("image_id", 0)),
                                 str(job.get("kind", "?")),
                                 f"{type(exc).__name__}: {exc}")
                if os.environ.get("PCE_DEBUG"):
                    traceback.print_exc()
            finally:
                self._done += 1
                self.counts.emit(self._done, self._submitted)
            if self._submitted and self._done >= self._submitted:
                self._reset_batch()
        self._release()

    def _reset_batch(self):
        self._submitted = 0
        self._done = 0
        self._cancel_batch = False
        if setting("unload_models_when_idle", True):
            self._release()
        self.progress.emit("")
        self.idle.emit()

    def _release(self):
        self._tagger = None
        self._upscaler = None

    def _do(self, job):
        kind = job.get("kind")
        image_id = int(job.get("image_id", 0))
        path = job.get("path")
        name = Path(path).name if path else "?"
        left = self.pending()

        if kind == "tag":
            self.progress.emit(f"🤖 Tagging {name}"
                               + (f"   ({left} queued)" if left else ""))
            if self._tagger is None:
                self.progress.emit("🤖 Loading tagger model…")
                self._tagger = Tagger()
            result = self._tagger.tag(path)
            if self._cancelled():
                raise Cancelled()
            self.tagged.emit(image_id, result)
            self.progress.emit(
                f"🤖 {name}: {result['rating']}, {len(result['tags'])} tag(s)")

        elif kind == "upscale":
            if self._upscaler is None:
                self.progress.emit("⬆ Loading upscale model…")
                self._upscaler = Upscaler()
            scale = int(job.get("scale") or setting("upscale_scale", 2) or 2)
            out = job.get("out") or upscale_output_path(path, scale)

            def _p(done, total):
                self.progress.emit(
                    f"⬆ Upscaling {name} ×{scale} — tile {done}/{total}"
                    + (f"   ({left} queued)" if left else ""))

            self._upscaler.upscale(path, out, target_scale=scale,
                                   progress=_p, cancelled=self._cancelled)
            self.upscaled.emit(image_id, str(path), str(out))
            self.progress.emit(f"⬆ {name} → {Path(out).name}")

        else:
            raise ValueError(f"unknown job kind: {kind!r}")


# ─── Model downloader ──────────────────────────────────────────────────────

class Downloader(QThread):
    """Fetches the tagger files with stdlib urllib — no huggingface_hub needed.

    Writes to <name>.part and renames on success, so an interrupted download
    never looks like a valid model.
    """

    progress = pyqtSignal(str, int)            # message, percent (-1 unknown)
    done = pyqtSignal(bool, str)               # ok, message

    def __init__(self, targets=None, dest=None, parent=None):
        super().__init__(parent)
        self.targets = list(targets or TAGGER_DOWNLOADS)
        self.dest = Path(dest or tagger_dir())
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        import urllib.request
        self.dest.mkdir(parents=True, exist_ok=True)
        part = None
        try:
            for target in self.targets:
                if len(target) != 4:
                    raise ValueError(
                        "download targets must contain name, URL, size, and SHA-256")
                name, url, expected_size, expected_sha256 = target
                final = self.dest / name
                if final.is_file():
                    self.progress.emit(f"{name} — verifying existing file", -1)
                    valid, reason = verify_file(
                        final, expected_size, expected_sha256)
                    if valid:
                        self.progress.emit(f"{name} already present and verified", 100)
                        continue
                    self.progress.emit(f"{name} invalid ({reason}); downloading again", -1)
                part = final.with_suffix(final.suffix + ".part")
                req = urllib.request.Request(
                    url, headers={"User-Agent": "PerchanceAppEngine/1.0"})
                with urllib.request.urlopen(req, timeout=60) as resp, \
                        open(part, "wb") as fh:
                    total = int(resp.headers.get("Content-Length") or expected_size or 0)
                    got = 0
                    last = 0.0
                    while True:
                        if self._stop:
                            raise Cancelled()
                        chunk = resp.read(262_144)
                        if not chunk:
                            break
                        fh.write(chunk)
                        got += len(chunk)
                        now = time.monotonic()
                        if now - last > 0.2:
                            last = now
                            pct = int(got * 100 / total) if total else -1
                            self.progress.emit(
                                f"{name} — {human(got)}"
                                + (f" / {human(total)}" if total else ""),
                                min(pct, 99) if pct >= 0 else -1)
                valid, reason = verify_file(part, expected_size, expected_sha256)
                if not valid:
                    raise OSError(f"{name}: verification failed: {reason}")
                part.replace(final)
                self.progress.emit(f"{name} — downloaded and verified", 100)
            self.done.emit(True, "Model files downloaded and SHA-256 verified")
        except Cancelled:
            if part is not None:
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
            self.done.emit(False, "Download cancelled")
        except Exception as exc:                     # noqa: BLE001
            if part is not None:
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
            self.done.emit(False, f"{type(exc).__name__}: {exc}")


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ─── CLI (handy for checking a machine without opening the gallery) ────────

def _cli(argv):
    if "--help" in argv or "-h" in argv:
        print(__doc__.strip())
        print("\nUsage:\n"
              "  imagetools.py --status\n"
              "  imagetools.py --verify              (check tagger files)\n"
              "  imagetools.py --download            (tagger model, ~467 MB)\n"
              "  imagetools.py --tag <image> [...]\n"
              "  imagetools.py --upscale <image> [--scale 2|4]\n")
        return 0

    if "--verify" in argv:
        failures = 0
        base = tagger_dir()
        for name, _url, expected_size, expected_sha256 in TAGGER_DOWNLOADS:
            ok, detail = verify_file(base / name, expected_size, expected_sha256)
            print(f"{name}: {'OK' if ok else 'FAILED'} — {detail}")
            failures += 0 if ok else 1
        return 0 if failures == 0 else 1

    if "--status" in argv or len(argv) == 0:
        print(status_text())
        print("models dir:", models_dir())
        if available():
            print("onnxruntime:", _ORT.__version__, "| numpy:", _NP.__version__)
            print("installed providers:", ", ".join(_ORT.get_available_providers()))
        return 0

    if "--download" in argv:
        from PyQt6.QtCore import QCoreApplication, QEventLoop
        app = QCoreApplication.instance() or QCoreApplication(sys.argv)
        dl = Downloader()
        loop = QEventLoop()
        dl.progress.connect(lambda m, p: print(f"  {m}", end="\r", flush=True))
        dl.done.connect(lambda ok, m: (print("\n" + m), loop.quit()))
        dl.start()
        loop.exec()
        return 0

    if "--tag" in argv:
        paths = [a for a in argv[argv.index("--tag") + 1:] if not a.startswith("--")]
        tagger = Tagger()
        for p in paths:
            r = tagger.tag(p)
            print(f"\n{p}\n  rating: {r['rating']}  "
                  f"({', '.join(f'{k} {v:.2f}' for k, v in r['rating_scores'].items())})")
            print("  tags:", ", ".join(r["tags"]) or "(none over threshold)")
        return 0

    if "--upscale" in argv:
        rest = argv[argv.index("--upscale") + 1:]
        paths = [a for a in rest if not a.startswith("--")]
        scale = 2
        if "--scale" in argv:
            try:
                scale = int(argv[argv.index("--scale") + 1])
            except (IndexError, ValueError):
                pass
        paths = [p for p in paths if p != str(scale)]
        up = Upscaler()
        print(f"model: {up.model_path.name} (native ×{up.scale})")
        for p in paths:
            out = upscale_output_path(p, scale)
            up.upscale(p, out, target_scale=scale,
                       progress=lambda d, t: print(f"  tile {d}/{t}",
                                                   end="\r", flush=True))
            print(f"\n  {p} -> {out}")
        return 0

    print("unrecognised arguments; try --help")
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
