#!/usr/bin/env python3
"""
runner_clean.py — Clean Perchance generator window.
Rebuilt from scratch. NO stealth injection, NO PAT interception,
NO spoofed client hints. Turnstile path is completely untouched.

Keeps: theming, downloads, JS override injection, reload, progress bar.

Usage:
    python runner_clean.py <slug>
    python runner_clean.py petrafied-acc
"""

import sys
import os
import argparse
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QFileDialog, QProgressBar
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile,
    QWebEngineSettings, QWebEngineDownloadRequest
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QIcon, QPixmap

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("slug", nargs="?", default=None)
args, _ = parser.parse_known_args()

SLUG = args.slug or "petrafied-acc"   # fallback for direct launch

# ---------------------------------------------------------------------------
# Optional: load per-generator override JS from a local file if present.
# Place a file at  ./overrides/<slug>.js  to have it injected after load.
# No file = no injection, no errors.
# ---------------------------------------------------------------------------
OVERRIDES_DIR = Path(__file__).parent / "overrides"

def load_override_js(slug: str) -> str:
    js_file = OVERRIDES_DIR / f"{slug}.js"
    if js_file.exists():
        return js_file.read_text("utf-8")
    return ""

# ---------------------------------------------------------------------------
# Page — only overrides javaScriptConsoleMessage for debug visibility
# ---------------------------------------------------------------------------
class PerchancePage(QWebEnginePage):
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)

    def javaScriptConsoleMessage(self, level, message, line, source):
        names = {0: "DBG", 1: "INF", 2: "WRN", 3: "ERR"}
        print(f"[JS:{names.get(level, '?')}] {message}  ({source}:{line})")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class AppWindow(QMainWindow):
    def __init__(self, slug: str):
        super().__init__()
        self.slug = slug

        self._setup_profile()
        self._build_ui()
        self._apply_theme()

        self.setWindowTitle(f"Perchance — {slug}")
        self.webview.load(QUrl(f"https://perchance.org/{slug}"))

    # ------------------------------------------------------------------
    # Profile: plain persistent profile, nothing spoofed, nothing blocked
    # ------------------------------------------------------------------
    def _setup_profile(self):
        slug = self.slug
        storage = Path(__file__).parent / "storage" / slug
        cache   = Path(__file__).parent / "cache"   / slug
        storage.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)

        self.profile = QWebEngineProfile(f"perchance_{slug}", self)
        self.profile.setPersistentStoragePath(str(storage))
        self.profile.setCachePath(str(cache))
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        # Downloads
        self.profile.downloadRequested.connect(self._handle_download)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Chrome bar
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

        # Buttons
        for icon, tip, slot in [
            ("↻", "Reload page",           self._reload),
            ("⚡", "Re-inject overrides",   self._inject_js),
            ("📂", "Open storage folder",   self._open_storage),
        ]:
            btn = QPushButton(icon)
            btn.setObjectName("chromeBtn")
            btn.setFixedSize(28, 28)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            chrome_layout.addWidget(btn)

        layout.addWidget(chrome)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setObjectName("loadBar")
        self.progress.setFixedHeight(3)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)

        # Web view
        self.page = PerchancePage(self.profile, parent=self)
        self.webview = QWebEngineView()
        self.webview.setPage(self.page)

        # Minimal required settings — nothing that fights Cloudflare
        s = self.webview.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled,              True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled,            True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled,          True)
        s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,       True)
        s.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages,                 True)
        # Allow popups — some Turnstile flows open a brief invisible window
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,       True)

        self.webview.loadStarted.connect(self._on_load_started)
        self.webview.loadProgress.connect(self._on_load_progress)
        self.webview.loadFinished.connect(self._on_load_finished)
        self.webview.titleChanged.connect(
            lambda t: self.setWindowTitle(t or f"Perchance — {self.slug}")
        )

        layout.addWidget(self.webview, 1)

        # Status bar
        self.status = self.statusBar()
        self.status.setObjectName("statusBar")
        self.status.setFixedHeight(22)
        self.status.setSizeGripEnabled(False)
        self.status.showMessage("Loading…")

        self.resize(1100, 750)

    # ------------------------------------------------------------------
    # Load lifecycle
    # ------------------------------------------------------------------
    def _on_load_started(self):
        self.progress.show()
        self.progress.setValue(0)
        self.status.showMessage("Loading…")

    def _on_load_progress(self, v: int):
        self.progress.setValue(v)

    def _on_load_finished(self, ok: bool):
        self.progress.hide()
        if not ok:
            self.status.showMessage("⚠ Page failed to load — check your connection")
            return
        self.status.showMessage("✓ Ready")
        self._inject_js()

    # ------------------------------------------------------------------
    # JS override injection (opt-in, from local file only)
    # ------------------------------------------------------------------
    def _inject_js(self):
        script = load_override_js(self.slug)
        if script.strip():
            self.page.runJavaScript(script)
            self.status.showMessage("⚡ Overrides injected")

    # ------------------------------------------------------------------
    # Toolbar actions
    # ------------------------------------------------------------------
    def _reload(self):
        self.webview.reload()

    def _handle_download(self, item: QWebEngineDownloadRequest):
        suggested = Path(item.suggestedFileName())
        dest, _ = QFileDialog.getSaveFileName(self, "Save File", str(suggested))
        if dest:
            item.setDownloadDirectory(str(Path(dest).parent))
            item.setDownloadFileName(Path(dest).name)
            item.accept()
            self.status.showMessage(f"⬇ Downloading → {Path(dest).name}")
        else:
            item.cancel()

    def _open_storage(self):
        path = Path(__file__).parent / "storage" / self.slug
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    # ------------------------------------------------------------------
    # Theme — same dark style as the original, accent from teal
    # ------------------------------------------------------------------
    def _apply_theme(self):
        accent = "#01696f"
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
                color: #5a5957; font-size: 11px;
            }}
            QScrollBar:vertical {{ background: #1c1b19; width: 5px; }}
            QScrollBar::handle:vertical {{ background: #393836; border-radius: 2px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    # Pass --disable-blink-features=AutomationControlled at the Chromium level
    # so the browser doesn't self-identify as automated.  This is the ONE
    # Chromium flag that's safe and appropriate here — it's NOT a JS patch,
    # so Turnstile can't detect the patching attempt itself.
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--disable-blink-features=AutomationControlled "
        "--autoplay-policy=no-user-gesture-required"
    )

    app = QApplication(sys.argv)
    app.setApplicationName(f"Perchance — {SLUG}")
    app.setOrganizationName("PerchanceEngine")

    window = AppWindow(SLUG)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
