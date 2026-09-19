#!/usr/bin/env python3
"""
runner.py — Perchance generator window (Perchance App Engine).

NO stealth injection, NO PAT interception, NO spoofed client hints.
The Turnstile path is left completely untouched.

What this build does:
  * Reads every relevant preference from settings.json (configurate.py):
    window size / geometry memory / maximized / zoom / devtools / popups /
    user agent / download folder / ask-where-to-save / unique filenames.
  * Injects BOTH override scripts on every page load:
      <root>/global-overrides.js   (or global-override.js)
      <root>/gens/<slug>/overrides.js
    Installed as profile scripts, so they also run after in-page navigation
    and inside spawned pop-up windows.  ⚡ re-injects them on demand.
  * "Inject URL data" modal (🔗 / Ctrl+L): paste a full share link such as
      https://perchance.org/aschat?data=Chloe~0cce02f9…gz
    and everything after the slug (?data=…, #hash, extra path) is attached to
    the current generator URL.  The scheme/host/slug part is discarded, so a
    link for a different slug still loads in this generator.
  * Pop-ups open as REAL SEPARATE WINDOWS (never tabs), sharing the profile.

Usage:
    python runner.py <slug> [--root <program root>] [--url-suffix "?data=…"]
"""

import sys
import os
import argparse
import json
from pathlib import Path


# ─── Arguments ────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("slug", nargs="?", default=None)
parser.add_argument("--root", default=None,
                    help="program root (folder holding config.py)")
parser.add_argument("--url-suffix", default="",
                    help='query/hash to append on first load, e.g. "?data=…"')
args, _unknown = parser.parse_known_args()

SLUG = args.slug or "petrafied-acc"      # fallback for a bare direct launch
HERE = Path(__file__).resolve().parent   # …/app_runner


# ─── Program root + config.py ────────────────────────────────────────────────

def _find_root() -> Path:
    """Locate the folder that holds config.py."""
    candidates = []
    if args.root:
        candidates.append(Path(args.root))
    candidates += [HERE.parent, HERE, Path.cwd()]
    for c in candidates:
        try:
            if (c / "config.py").exists():
                return c.resolve()
        except OSError:
            continue
    return HERE.parent.resolve()


APP_ROOT = _find_root()
for p in (str(APP_ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import config                                    # type: ignore
except Exception:                                    # noqa: BLE001
    config = None


def gen_dir(slug: str) -> Path:
    if config:
        return config.gen_dir(slug)
    return APP_ROOT / "gens" / slug


def _sub(slug: str, name: str) -> Path:
    d = APP_ROOT / "data" / slug / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def storage_dir(slug: str) -> Path:
    return config.gen_storage_dir(slug) if config else _sub(slug, "storage")


def cache_dir(slug: str) -> Path:
    return config.gen_cache_dir(slug) if config else _sub(slug, "cache")


def files_dir(slug: str) -> Path:
    return config.gen_files_dir(slug) if config else _sub(slug, "files")


def data_dir(slug: str) -> Path:
    if config:
        return config.gen_data_dir(slug)
    d = APP_ROOT / "data" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─── Settings bridge (configurate.py) ────────────────────────────────────────
# Preferences live in <root>/settings.json and configurate.py is the editor.
# If configurate.py is missing the runner still works on the fallbacks below.

try:
    from configurate import (                        # type: ignore
        settings as _settings, chromium_flags as _chromium_flags
    )

    def setting(key, default=None):
        value = _settings.get(key, default)
        return default if value is None else value

    def reload_settings():
        try:
            _settings.load()
        except Exception:                            # noqa: BLE001
            pass

    def chromium_flags() -> str:
        return _chromium_flags()
except Exception:                                    # noqa: BLE001
    def setting(key, default=None):
        return default

    def reload_settings():
        pass

    def chromium_flags() -> str:
        return ""


# QtWebEngine reads Chromium flags during initialization. Apply the safe built-in
# default and explicit user configuration before importing any Qt modules.
_base_flags = "--autoplay-policy=no-user-gesture-required"
_extra_flags = chromium_flags()
_existing_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(
    value for value in (_existing_flags, _base_flags, _extra_flags) if value
).strip()
if setting("enable_devtools", False):
    os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9222")

from PyQt6.QtCore import Qt, QUrl, QTimer, QEvent  # noqa: E402
from PyQt6.QtGui import QIcon, QPixmap, QKeySequence, QShortcut, QColor  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QFileDialog, QProgressBar,
    QDialog, QLineEdit, QDialogButtonBox, QMessageBox
)
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWebEngineCore import (  # noqa: E402
    QWebEnginePage, QWebEngineProfile, QWebEngineSettings,
    QWebEngineScript, QWebEngineDownloadRequest
)
from core_utils import (  # noqa: E402
    build_url, extract_suffix, sanitize_filename, unique_path,
)


def _int_setting(key, default, low, high) -> int:
    try:
        return max(low, min(high, int(setting(key, default))))
    except (TypeError, ValueError):
        return default


# ─── Override scripts (global + per generator) ───────────────────────────────

def _global_js_paths():
    """config.GLOBAL_JS plus both spellings, so either filename works."""
    paths = []
    if config and getattr(config, "GLOBAL_JS", None):
        paths.append(Path(config.GLOBAL_JS))
    paths += [APP_ROOT / "global-overrides.js", APP_ROOT / "global-override.js"]
    seen, out = set(), []
    for p in paths:
        key = str(p).casefold()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def override_sources(slug: str):
    """[(label, path, source)] for every override file that exists.

    Global first, then the generator's own overrides.js, so a per-app script
    can override what the global one set up.
    """
    found = []
    for path in _global_js_paths():
        try:
            if path.is_file():
                text = path.read_text("utf-8")
                if text.strip():
                    found.append(("global", path, text))
                break                                # first match wins
        except OSError:
            continue

    local_candidates = [
        gen_dir(slug) / "overrides.js",
        HERE / "overrides" / f"{slug}.js",           # legacy location
    ]
    for path in local_candidates:
        try:
            if path.is_file():
                text = path.read_text("utf-8")
                if text.strip():
                    found.append(("local", path, text))
                break
        except OSError:
            continue
    return found


def _wrap(label: str, path: Path, source: str) -> str:
    """Isolate each script so a syntax error in one can't kill the other."""
    tag = json.dumps(f"{label}:{path.name}")
    return (
        "(function(){try{\n"
        f"{source}\n"
        "}catch(e){console.error('[override '+" + tag + "+'] '+e);}})();"
    )


# ─── URL suffix parsing (the "inject URL" feature) ───────────────────────────

class InjectUrlDialog(QDialog):
    """Paste a share link; keep only the part after the slug."""

    def __init__(self, slug: str, current: str, parent=None):
        super().__init__(parent)
        self.slug = slug
        self.setWindowTitle("Inject URL data")
        self.setMinimumWidth(620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        info = QLabel(
            "Paste a Perchance share link or just the data part.\n"
            "The https://perchance.org/<slug> portion is ignored — only what "
            "comes after it (?data=…, #hash, extra path) is attached to this "
            "generator's URL."
        )
        info.setTextFormat(Qt.TextFormat.PlainText)   # keep <slug> literal
        info.setObjectName("infoLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "https://perchance.org/aschat?data=Chloe~0cce02f9….gz")
        self.input.textChanged.connect(self._update_preview)
        layout.addWidget(self.input)

        layout.addWidget(QLabel("Will load:"))
        self.preview = QLabel()
        self.preview.setObjectName("previewLabel")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.preview)

        self.current_label = QLabel(f"Current: {current}")
        self.current_label.setObjectName("infoLabel")
        self.current_label.setWordWrap(True)
        layout.addWidget(self.current_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Load")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # pre-fill from the clipboard when it already holds a perchance link
        clip = QApplication.clipboard().text().strip()
        if "perchance.org" in clip or clip.startswith(("?", "#")):
            self.input.setText(clip)
            self.input.selectAll()
        self._update_preview()

    def _update_preview(self):
        raw = self.input.text().strip()
        suffix = extract_suffix(raw)
        if not raw:
            self.preview.setText("—")
            self.preview.setProperty("bad", False)
        elif suffix:
            self.preview.setText(build_url(self.slug, suffix))
            self.preview.setProperty("bad", False)
        else:
            self.preview.setText(
                "No data found after the slug — nothing to attach.")
            self.preview.setProperty("bad", True)
        self.preview.style().unpolish(self.preview)
        self.preview.style().polish(self.preview)

    def _accept(self):
        if not extract_suffix(self.input.text()):
            QMessageBox.warning(
                self, "Nothing to inject",
                "Could not find a ?query, #hash or extra path after the slug.\n\n"
                "Example of a link that works:\n"
                "https://perchance.org/aschat?data=Chloe~0cce02f9e1ef.gz"
            )
            return
        self.accept()

    def suffix(self) -> str:
        return extract_suffix(self.input.text())


# ─── Download filename helpers ───────────────────────────────────────────────

# ─── Page ────────────────────────────────────────────────────────────────────

class PerchancePage(QWebEnginePage):
    """Only two overrides: console logging and pop-up handling."""

    def __init__(self, profile, owner=None, parent=None):
        super().__init__(profile, parent)
        self.owner = owner

    def javaScriptConsoleMessage(self, level, message, line, source):
        names = {0: "DBG", 1: "INF", 2: "WRN", 3: "ERR"}
        try:
            level_name = names.get(level.value if hasattr(level, "value")
                                   else int(level), "?")
        except Exception:                            # noqa: BLE001
            level_name = "?"
        print(f"[JS:{level_name}] {message}  ({source}:{line})")

    def createWindow(self, window_type):
        """Anything the page tries to open becomes a separate OS window.

        Returning a page from a brand-new top-level window is what makes
        window.open() / target=_blank spawn a real window instead of a tab.
        """
        if self.owner is None:
            return None
        return self.owner.spawn_child_window(window_type)


# ─── Child (pop-up) window ───────────────────────────────────────────────────

_OPEN_WINDOWS = []          # module-level refs so windows aren't GC'd


class PopupWindow(QMainWindow):
    """A real separate window hosting a page the site asked us to open."""

    def __init__(self, owner: "AppWindow"):
        super().__init__()          # no parent → its own taskbar window
        self.owner = owner
        self.slug = owner.slug
        self.profile = owner.profile
        self._reserved_downloads = owner._reserved_downloads

        self.setWindowTitle(f"Perchance — {self.slug} (window)")
        if owner.windowIcon() and not owner.windowIcon().isNull():
            self.setWindowIcon(owner.windowIcon())

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("chrome")
        bar.setFixedHeight(30)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(10, 0, 6, 0)
        bar_layout.setSpacing(6)
        self.title_label = QLabel("opened by the page")
        self.title_label.setObjectName("chromeTitle")
        bar_layout.addWidget(self.title_label)
        bar_layout.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setObjectName("chromeBtn")
        close_btn.setFixedSize(24, 24)
        close_btn.setToolTip("Close this window")
        close_btn.clicked.connect(self.close)
        bar_layout.addWidget(close_btn)
        layout.addWidget(bar)

        self.page = PerchancePage(self.profile, owner=owner, parent=self)
        self.webview = QWebEngineView()
        self.webview.setPage(self.page)
        apply_web_attributes(self.webview.settings())
        self.page.setZoomFactor(owner.page.zoomFactor())
        self.webview.urlChanged.connect(
            lambda u: self.title_label.setText(u.toString()[:110]))
        self.webview.titleChanged.connect(
            lambda t: self.setWindowTitle(t or f"Perchance — {self.slug}"))
        self.page.windowCloseRequested.connect(self.close)
        layout.addWidget(self.webview, 1)

        self.setStyleSheet(owner.styleSheet())
        w = _int_setting("window_width", 1280, 480, 7680)
        h = _int_setting("window_height", 860, 360, 4320)
        self.resize(max(520, int(w * 0.72)), max(400, int(h * 0.72)))
        self.move(owner.x() + 48, owner.y() + 48)

    def closeEvent(self, e):
        if self in _OPEN_WINDOWS:
            _OPEN_WINDOWS.remove(self)
        self.webview.setPage(None)
        self.page.deleteLater()
        super().closeEvent(e)


# ─── Shared web attributes ───────────────────────────────────────────────────

def apply_web_attributes(s: QWebEngineSettings):
    """Minimal required settings — nothing that fights Cloudflare."""
    A = QWebEngineSettings.WebAttribute
    s.setAttribute(A.JavascriptEnabled, True)
    s.setAttribute(A.LocalStorageEnabled, True)
    s.setAttribute(A.LocalContentCanAccessRemoteUrls, True)
    s.setAttribute(A.ScrollAnimatorEnabled, True)
    s.setAttribute(A.FullScreenSupportEnabled, True)
    s.setAttribute(A.AutoLoadImages, True)
    s.setAttribute(A.PlaybackRequiresUserGesture, False)
    # Pop-ups: allowed unless the user blocked them. Some Turnstile flows open
    # a brief window, so blocking can break sign-in.
    s.setAttribute(A.JavascriptCanOpenWindows,
                   not bool(setting("block_popups", False)))
    if setting("enable_devtools", False):
        s.setAttribute(A.JavascriptCanAccessClipboard, True)


# ─── Main window ─────────────────────────────────────────────────────────────

class AppWindow(QMainWindow):
    def __init__(self, slug: str, first_suffix: str = ""):
        super().__init__()
        self.slug = slug
        self.meta = config.read_meta(slug) if config else {"name": slug}
        self.accent = self._accent()
        self._reserved_downloads = set()
        self._dev_window = None
        self._script_labels = []

        self._setup_profile()
        self._build_ui()
        self._apply_theme()
        self._install_scripts()
        self._restore_geometry()

        icon = gen_dir(slug) / "favicon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        self.setWindowTitle(f"Perchance — {self.meta.get('name', slug)}")
        self.load_url(build_url(slug, first_suffix))

    # -- appearance -------------------------------------------------------
    def _accent(self) -> str:
        c = QColor(str(self.meta.get("color", "") or ""))
        return c.name() if c.isValid() else "#01696f"

    # -- profile ----------------------------------------------------------
    def _setup_profile(self):
        slug = self.slug
        # parented to the application, not the window: Qt warns ("Expect
        # troubles") if a profile outlives its pages during teardown
        self.profile = QWebEngineProfile(
            f"perchance_{slug}", QApplication.instance())
        self.profile.setPersistentStoragePath(str(storage_dir(slug)))
        self.profile.setCachePath(str(cache_dir(slug)))
        self.profile.setHttpCacheType(
            QWebEngineProfile.HttpCacheType.DiskHttpCache)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        # "User agent override" (settings → Generators); blank = Qt default
        ua = str(setting("user_agent", "") or "").strip()
        if ua:
            self.profile.setHttpUserAgent(ua)
        self.profile.setDownloadPath(str(self.download_dir()))
        self.profile.downloadRequested.connect(self._handle_download)

    # -- override script installation -------------------------------------
    def _install_scripts(self):
        """Install global + per-generator overrides as profile scripts.

        Profile scripts re-run on every document (including pop-up windows and
        in-page navigations), which is what makes the injection reliable.
        """
        collection = self.profile.scripts()
        for name in self._script_labels:
            for existing in collection.find(name):
                collection.remove(existing)
        self._script_labels = []

        for label, path, source in override_sources(self.slug):
            script = QWebEngineScript()
            name = f"pce-{label}-override"
            script.setName(name)
            script.setSourceCode(_wrap(label, path, source))
            script.setInjectionPoint(
                QWebEngineScript.InjectionPoint.DocumentReady)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            collection.insert(script)
            self._script_labels.append(name)

        if hasattr(self, "status"):
            if self._script_labels:
                kinds = ", ".join(n.split("-")[1] for n in self._script_labels)
                self.status.showMessage(f"⚡ Overrides armed ({kinds})", 4000)
            else:
                self.status.showMessage("No override scripts found", 4000)

    def _inject_js(self):
        """Manually run both override files against the live page right now."""
        sources = override_sources(self.slug)
        if not sources:
            self.status.showMessage(
                "No override scripts found — expected "
                f"{_global_js_paths()[0].name} or gens/{self.slug}/overrides.js",
                6000)
            return
        for label, path, source in sources:
            self.page.runJavaScript(_wrap(label, path, source))
        kinds = " + ".join(label for label, _p, _s in sources)
        self.status.showMessage(f"⚡ Injected {kinds} overrides", 4000)

    # -- UI ---------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        chrome = QFrame()
        chrome.setObjectName("chrome")
        chrome.setFixedHeight(38)
        chrome_layout = QHBoxLayout(chrome)
        chrome_layout.setContentsMargins(10, 0, 6, 0)
        chrome_layout.setSpacing(6)

        self.title_label = QLabel(f"perchance.org/{self.slug}")
        self.title_label.setObjectName("chromeTitle")
        chrome_layout.addWidget(self.title_label)
        chrome_layout.addStretch()

        buttons = [
            ("🔗", "Inject URL data  (Ctrl+L)", self._inject_url),
            ("⌂", "Back to the plain generator URL", self._go_home),
            ("↻", "Reload page  (F5)", self._reload),
            ("⚡", "Re-inject overrides  (Ctrl+R)", self._inject_js),
            ("📂", "Open files folder", self._open_files),
        ]
        if setting("enable_devtools", False):
            buttons.append(("🛠", "Developer tools  (F12)", self._toggle_devtools))

        for icon, tip, slot in buttons:
            btn = QPushButton(icon)
            btn.setObjectName("chromeBtn")
            btn.setFixedSize(28, 28)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            chrome_layout.addWidget(btn)

        layout.addWidget(chrome)

        self.progress = QProgressBar()
        self.progress.setObjectName("loadBar")
        self.progress.setFixedHeight(3)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.page = PerchancePage(self.profile, owner=self, parent=self)
        self.webview = QWebEngineView()
        self.webview.setPage(self.page)
        apply_web_attributes(self.webview.settings())

        # "Page zoom (%)" (settings → Generators)
        self.page.setZoomFactor(_int_setting("zoom_factor", 100, 50, 250) / 100)

        self.webview.loadStarted.connect(self._on_load_started)
        self.webview.loadProgress.connect(self._on_load_progress)
        self.webview.loadFinished.connect(self._on_load_finished)
        self.webview.urlChanged.connect(self._on_url_changed)
        self.webview.titleChanged.connect(
            lambda t: self.setWindowTitle(
                t or f"Perchance — {self.meta.get('name', self.slug)}")
        )
        layout.addWidget(self.webview, 1)

        self.status = self.statusBar()
        self.status.setObjectName("statusBar")
        self.status.setFixedHeight(22)
        self.status.setSizeGripEnabled(False)
        self.status.showMessage("Loading…")

        for keys, slot in [
            ("Ctrl+L", self._inject_url),
            ("F5", self._reload),
            ("Ctrl+R", self._inject_js),
            ("F12", self._toggle_devtools),
            ("Ctrl+0", lambda: self.page.setZoomFactor(
                _int_setting("zoom_factor", 100, 50, 250) / 100)),
        ]:
            QShortcut(QKeySequence(keys), self, slot)

        # default size; _restore_geometry may replace it
        self.resize(_int_setting("window_width", 1280, 480, 7680),
                    _int_setting("window_height", 860, 360, 4320))

    # -- geometry ---------------------------------------------------------
    def _geometry_file(self) -> Path:
        return data_dir(self.slug) / "window.json"

    def _restore_geometry(self):
        # "Remember window size and position" (settings → Generators)
        if setting("remember_geometry", True):
            try:
                saved = json.loads(self._geometry_file().read_text("utf-8"))
                if all(k in saved for k in ("x", "y", "w", "h")):
                    self.resize(int(saved["w"]), int(saved["h"]))
                    self.move(int(saved["x"]), int(saved["y"]))
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if setting("start_maximized", False):
            self.setWindowState(self.windowState()
                                | Qt.WindowState.WindowMaximized)

    def _save_geometry(self):
        if not setting("remember_geometry", True):
            return
        if self.isMaximized() or self.isFullScreen():
            return                                   # keep the restored size
        geo = self.normalGeometry()
        try:
            self._geometry_file().write_text(json.dumps({
                "x": geo.x(), "y": geo.y(),
                "w": geo.width(), "h": geo.height(),
            }, indent=2), encoding="utf-8")
        except OSError:
            pass

    def closeEvent(self, e):
        self._save_geometry()
        for win in list(_OPEN_WINDOWS):
            win.close()
        if self._dev_window is not None:
            self.page.setDevToolsPage(None)
            self._dev_window.close()
            self._dev_window = None
        # tear the page down before the profile goes away
        self.webview.setPage(None)
        self.page.deleteLater()
        super().closeEvent(e)

    def changeEvent(self, e):
        super().changeEvent(e)
        # picking up settings.json edits made while this window was open
        if e.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            QTimer.singleShot(0, self._resync_settings)

    def _resync_settings(self):
        reload_settings()
        zoom = _int_setting("zoom_factor", 100, 50, 250) / 100
        if abs(self.page.zoomFactor() - zoom) > 0.001:
            self.page.setZoomFactor(zoom)
        apply_web_attributes(self.webview.settings())

    # -- pop-ups ----------------------------------------------------------
    def spawn_child_window(self, window_type):
        """Called by PerchancePage.createWindow — always a separate window."""
        if setting("block_popups", False):
            self.status.showMessage(
                "🚫 Pop-up blocked (turn off 'Block pop-up windows' in "
                "Configure to allow it)", 6000)
            return None

        win = PopupWindow(self)
        _OPEN_WINDOWS.append(win)
        win.show()
        win.raise_()
        win.activateWindow()
        label = {
            QWebEnginePage.WebWindowType.WebBrowserWindow: "window",
            QWebEnginePage.WebWindowType.WebBrowserTab: "tab → window",
            QWebEnginePage.WebWindowType.WebDialog: "dialog → window",
            QWebEnginePage.WebWindowType.WebBrowserBackgroundTab:
                "background tab → window",
        }.get(window_type, "window")
        self.status.showMessage(f"🗗 Page opened a new {label}", 5000)
        return win.page

    # -- load lifecycle ---------------------------------------------------
    def load_url(self, url: str):
        self.webview.load(QUrl(url))

    def _on_load_started(self):
        self.progress.show()
        self.progress.setValue(0)
        self.status.showMessage("Loading…")

    def _on_load_progress(self, v: int):
        self.progress.setValue(v)

    def _on_load_finished(self, ok: bool):
        self.progress.hide()
        if not ok:
            self.status.showMessage(
                "⚠ Page failed to load — check your connection")
            return
        # The override files already ran at DocumentReady as profile scripts,
        # so nothing is injected again here — running them twice would make a
        # script that adds UI add it twice. Use ⚡ / Ctrl+R to re-run manually.
        armed = len(self._script_labels)
        self.status.showMessage(
            "✓ Ready" + (f"  ·  {armed} override file(s) injected" if armed else
                         "  ·  no override files found"))

    def _on_url_changed(self, url: QUrl):
        text = url.toString()
        self.title_label.setText(
            text.replace("https://", "")[:120] or f"perchance.org/{self.slug}")
        self.title_label.setToolTip(text)

    # -- toolbar actions --------------------------------------------------
    def _inject_url(self):
        dlg = InjectUrlDialog(self.slug, self.webview.url().toString(), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            target = build_url(self.slug, dlg.suffix())
            self.status.showMessage(f"→ {target}", 6000)
            self.load_url(target)

    def _go_home(self):
        self.load_url(build_url(self.slug))

    def _reload(self):
        self.webview.reload()

    def _toggle_devtools(self):
        # "Enable developer tools (F12)" (settings → Generators)
        if not setting("enable_devtools", False):
            self.status.showMessage(
                "Developer tools are disabled — enable them in Configure", 5000)
            return
        if self._dev_window is not None and self._dev_window.isVisible():
            self._dev_window.close()
            self._dev_window = None
            return

        window = QMainWindow(self)
        window.setWindowTitle(f"DevTools — {self.slug}")
        view = QWebEngineView()
        dev_page = QWebEnginePage(self.profile, view)
        view.setPage(dev_page)
        window.setCentralWidget(view)
        window.resize(1000, 620)
        self.page.setDevToolsPage(dev_page)
        window.show()
        self._dev_window = window

    def _open_files(self):
        path = self.download_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(path))              # noqa: S606
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
        except OSError as exc:
            self.status.showMessage(f"Could not open {path}: {exc}", 6000)

    # -- downloads --------------------------------------------------------
    def download_dir(self) -> Path:
        """"Download folder" (settings → Downloads); blank = data/<slug>/files."""
        configured = str(setting("download_dir", "") or "").strip()
        if configured:
            path = Path(os.path.expandvars(configured)).expanduser()
            try:
                path.mkdir(parents=True, exist_ok=True)
                return path
            except OSError:
                pass
        return files_dir(self.slug)

    def _handle_download(self, item: QWebEngineDownloadRequest):
        target_dir = self.download_dir()

        suggested = sanitize_filename(item.suggestedFileName())
        if not Path(suggested).suffix:
            # Perchance blob downloads sometimes arrive extension-less
            mime = (item.mimeType() or "").lower()
            suggested += {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/webp": ".webp",
                "image/gif": ".gif",
            }.get(mime, ".bin")

        # "Ask where to save each download" (settings → Downloads)
        if setting("ask_where_to_save", False):
            chosen, _filter = QFileDialog.getSaveFileName(
                self, "Save download", str(target_dir / suggested))
            if not chosen:
                item.cancel()
                self.status.showMessage("Download cancelled", 4000)
                return
            target = Path(chosen)
            target_dir = target.parent
            filename = target.name
        elif setting("unique_filenames", True):
            # "Never overwrite downloads"
            filename = unique_path(
                target_dir, suggested, self._reserved_downloads).name
        else:
            filename = suggested

        item.setDownloadDirectory(str(target_dir))
        item.setDownloadFileName(filename)
        item.accept()
        self.status.showMessage(f"⬇ Downloading → {filename}")

        def _on_finish():
            self._reserved_downloads.discard(filename.casefold())
            states = QWebEngineDownloadRequest.DownloadState
            if item.state() == states.DownloadCompleted:
                self.status.showMessage(f"✔ Saved → {filename}")
            elif item.state() == states.DownloadCancelled:
                self.status.showMessage("Download cancelled", 4000)
            else:
                self.status.showMessage(
                    f"✗ Download failed: {item.interruptReasonString()}")

        item.isFinishedChanged.connect(_on_finish)

    # -- theme ------------------------------------------------------------
    def _apply_theme(self):
        accent = self.accent
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: #1c1b19; color: #cdccca;
                font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 13px;
            }}
            #chrome {{ background: #171614; border-bottom: 1px solid #262523; }}
            #chromeTitle {{ font-weight: 600; font-size: 13px; color: #cdccca; }}
            #chromeBtn {{
                background: transparent; border: none;
                color: #797876; font-size: 15px; border-radius: 5px;
            }}
            #chromeBtn:hover  {{ background: #22211f; color: {accent}; }}
            #chromeBtn:pressed {{ background: #2d2c2a; }}
            #loadBar {{ background: #22211f; border: none; border-radius: 0; }}
            #loadBar::chunk {{ background: {accent}; border-radius: 0; }}
            #statusBar {{
                background: #171614; border-top: 1px solid #1f1e1c;
                color: #8b8a88; font-size: 11px;
            }}
            QScrollBar:vertical {{ background: #1c1b19; width: 5px; }}
            QScrollBar::handle:vertical {{ background: #393836; border-radius: 2px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QDialog {{ background: #1c1b19; }}
            QLabel {{ color: #cdccca; }}
            #infoLabel {{ color: #8b8a88; font-size: 11px; }}
            #previewLabel {{
                background: #22211f; border: 1px solid #393836;
                border-radius: 6px; padding: 7px 10px;
                font-family: Consolas, monospace; font-size: 11px;
                color: #7fc3cd;
            }}
            #previewLabel[bad="true"] {{ color: #e0806f; }}
            QLineEdit {{
                background: #22211f; border: 1px solid #393836;
                border-radius: 6px; padding: 7px 10px; color: #cdccca;
                selection-background-color: #313b3b;
            }}
            QLineEdit:focus {{ border-color: {accent}; }}
            QDialogButtonBox QPushButton {{
                background: #22211f; border: 1px solid #393836;
                border-radius: 6px; padding: 6px 16px; color: #cdccca;
                min-width: 80px;
            }}
            QDialogButtonBox QPushButton:hover {{
                background: #2d2c2a; border-color: {accent};
            }}
        """)


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(f"Jawless Perchance — {SLUG}")
    app.setOrganizationName("Jawless")

    window = AppWindow(SLUG, first_suffix=extract_suffix(args.url_suffix))
    if setting("start_maximized", False):
        window.showMaximized()
    else:
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
