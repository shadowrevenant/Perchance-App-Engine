#!/usr/bin/env python3
"""
runner.py — Standalone per-generator window.
Launched as a subprocess by the launcher (or directly via desktop shortcut).

Usage:
python runner.py <slug>
python runner.py <slug> --root /path/to/perchance-app
"""

import sys
import os

SAFE_CHROMIUM_FLAGS = "--autoplay-policy=no-user-gesture-required"
_SENTINEL = "__PCE_ENV_SET"

if _SENTINEL not in os.environ:
    import subprocess

    env = os.environ.copy()
    env[_SENTINEL] = "1"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = SAFE_CHROMIUM_FLAGS
    result = subprocess.run([sys.executable] + sys.argv, env=env)
    sys.exit(result.returncode)

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = SAFE_CHROMIUM_FLAGS

import argparse
from pathlib import Path

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("slug")
parser.add_argument("--root", default=None)
args, _ = parser.parse_known_args()

SLUG = args.slug
if args.root:
    sys.path.insert(0, str(Path(args.root).resolve()))
else:
    sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

import config  # noqa: E402

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QFileDialog, QProgressBar
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile,
    QWebEngineSettings, QWebEngineDownloadRequest,
    QWebEngineScript
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QIcon, QPixmap

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

STEALTH_JS = ""


def load_js(slug: str) -> str:
    parts = []

    if config.GLOBAL_JS.exists():
        parts.append("// === global-overrides.js ===\n" + config.GLOBAL_JS.read_text("utf-8"))

    gen_js = config.gen_dir(slug) / "overrides.js"
    if gen_js.exists():
        parts.append(f"// === {slug}/overrides.js ===\n" + gen_js.read_text("utf-8"))

    return "\n\n".join(parts)


def should_skip_injection(host: str) -> bool:
    host = (host or "").lower()
    return (
        host == "text-generation.perchance.org"
        or host.endswith("challenges.cloudflare.com")
        or host.endswith("cloudflare.com")
    )


class PerchancePage(QWebEnginePage):
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)

    def javaScriptConsoleMessage(self, level, message, line, source):
        names = {0: "DBG", 1: "INF", 2: "WRN", 3: "ERR"}
        print(f"[JS:{names.get(level, '?')}] {message} ({source}:{line})")

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        return True


class AppWindow(QMainWindow):
    def __init__(self, slug: str):
        super().__init__()
        self.slug = slug
        self.meta = config.read_meta(slug)

        self._setup_profile()
        self._build_ui()
        self._apply_theme()

        self.setWindowTitle(self.meta.get("name", slug))
        fav = config.gen_dir(slug) / "favicon.png"
        if fav.exists():
            self.setWindowIcon(QIcon(str(fav)))

        self.webview.load(QUrl(f"https://perchance.org/{slug}"))

    def _setup_profile(self):
        slug = self.slug

        self.profile = QWebEngineProfile(f"perchance_{slug}", self)
        self.profile.setPersistentStoragePath(str(config.gen_storage_dir(slug)))
        self.profile.setCachePath(str(config.gen_cache_dir(slug)))
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        self.profile.setHttpCacheMaximumSize(0)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self.profile.setHttpUserAgent(USER_AGENT)
        self.profile.downloadRequested.connect(self._handle_download)

        if STEALTH_JS.strip():
            stealth_script = QWebEngineScript()
            stealth_script.setName("stealth")
            stealth_script.setSourceCode(STEALTH_JS)
            stealth_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            stealth_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            stealth_script.setRunsOnSubFrames(False)
            self.profile.scripts().insert(stealth_script)

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

        fav = config.gen_dir(self.slug) / "favicon.png"
        if fav.exists():
            icon_label = QLabel()
            pm = QPixmap(str(fav)).scaled(
                20, 20,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            icon_label.setPixmap(pm)
            chrome_layout.addWidget(icon_label)

        self.title_label = QLabel(self.meta.get("name", self.slug))
        self.title_label.setObjectName("chromeTitle")
        chrome_layout.addWidget(self.title_label)
        chrome_layout.addStretch()

        reload_btn = QPushButton("↻")
        reload_btn.setObjectName("chromeBtn")
        reload_btn.setFixedSize(28, 28)
        reload_btn.setToolTip("Reload")
        reload_btn.clicked.connect(self._reload)

        inject_btn = QPushButton("⚡")
        inject_btn.setObjectName("chromeBtn")
        inject_btn.setFixedSize(28, 28)
        inject_btn.setToolTip("Re-inject overrides")
        inject_btn.clicked.connect(self._inject_js)

        folder_btn = QPushButton("📂")
        folder_btn.setObjectName("chromeBtn")
        folder_btn.setFixedSize(28, 28)
        folder_btn.setToolTip("Open data folder")
        folder_btn.clicked.connect(self._open_data_folder)

        chrome_layout.addWidget(reload_btn)
        chrome_layout.addWidget(inject_btn)
        chrome_layout.addWidget(folder_btn)
        layout.addWidget(chrome)

        self.progress = QProgressBar()
        self.progress.setObjectName("loadBar")
        self.progress.setFixedHeight(3)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.page = PerchancePage(self.profile, parent=self)
        self.webview = QWebEngineView()
        self.webview.setPage(self.page)

        s = self.webview.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)

        self.webview.loadStarted.connect(self._on_load_started)
        self.webview.loadProgress.connect(self._on_load_progress)
        self.webview.loadFinished.connect(self._on_load_finished)
        self.webview.titleChanged.connect(
            lambda t: self.setWindowTitle(t or self.meta.get("name", self.slug))
        )

        layout.addWidget(self.webview, 1)

        self.status = self.statusBar()
        self.status.setObjectName("statusBar")
        self.status.setFixedHeight(22)
        self.status.setSizeGripEnabled(False)
        self.status.showMessage("Loading…")

        self.resize(1100, 750)

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

    def _inject_js(self):
        host = self.page.url().host().lower()
        if should_skip_injection(host):
            self.status.showMessage(f"Skipping overrides on {host}")
            return

        script = load_js(self.slug)
        if script.strip():
            self.page.runJavaScript(script)
            self.status.showMessage("⚡ Overrides injected")

    def _reload(self):
        self.webview.reload()

    def _handle_download(self, item: QWebEngineDownloadRequest):
        files_dir = config.gen_files_dir(self.slug)
        suggested = files_dir / item.suggestedFileName()
        dest, _ = QFileDialog.getSaveFileName(self, "Save File", str(suggested))
        if dest:
            item.setDownloadDirectory(str(Path(dest).parent))
            item.setDownloadFileName(Path(dest).name)
            item.accept()
            self.status.showMessage(f"⬇ Downloading → {Path(dest).name}")
        else:
            item.cancel()

    def _open_data_folder(self):
        path = config.gen_data_dir(self.slug)
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    def closeEvent(self, e):
        self.webview.stop()
        super().closeEvent(e)

    def _apply_theme(self):
        accent = self.meta.get("color", "#01696f")
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
#chromeBtn:hover {{ background: #22211f; color: {accent}; }}
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


def main():
    if not SLUG:
        print("Usage: python runner.py <slug>")
        sys.exit(1)

    gdir = config.gen_dir(SLUG)
    if not gdir.exists():
        print(f"Generator '{SLUG}' not found in {config.GENS_DIR}")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName(config.read_meta(SLUG).get("name", SLUG))
    app.setOrganizationName("PerchanceEngine")

    window = AppWindow(SLUG)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()