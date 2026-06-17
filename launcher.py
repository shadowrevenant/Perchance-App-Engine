#!/usr/bin/env python3
"""
launcher.py — Perchance App Engine Launcher
Displays all installed generators as app cards; launches each in its own
isolated subprocess (separate window, profile, cache, and storage).
"""

import sys
import os
import subprocess
import shutil
import re
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))
import config
_SENTINEL = "__PCE_ENV_SET"
if _SENTINEL not in os.environ:
    env = os.environ.copy()
    env[_SENTINEL] = "1"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        "--autoplay-policy=no-user-gesture-required "
        "--disable-blink-features=AutomationControlled,TrustedDOMTypes "
        "--disable-features=IsolateOrigins,site-per-process "
        "--enable-features=NetworkService,NetworkServiceInProcess "
        "--allow-running-insecure-content "
        "--ignore-certificate-errors "
        "--ignore-ssl-errors"
    )
    import subprocess
    result = subprocess.run([sys.executable] + sys.argv, env=env)
    sys.exit(result.returncode)
    
# MUST be set before any Qt import — Qt reads this exactly once at startup
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--autoplay-policy=no-user-gesture-required "
    "--disable-blink-features=AutomationControlled,TrustedDOMTypes "
    "--disable-features=IsolateOrigins,site-per-process "
    "--enable-features=NetworkService,NetworkServiceInProcess "
    "--allow-running-insecure-content "
    "--ignore-certificate-errors "
    "--ignore-ssl-errors"
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile,
    QWebEngineSettings, QWebEngineScript,
    QWebEngineDownloadRequest   # renamed from QWebEngineDownloadItem
)
from PyQt6.QtCore import Qt, QSize, QProcess, QTimer, QPoint
from PyQt6.QtGui import QIcon, QPixmap, QColor, QFont, QPainter, QBrush, QPen
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel,
    QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea,
    QLineEdit, QPushButton, QDialog, QDialogButtonBox,
    QFormLayout, QTextEdit, QFileDialog, QMessageBox,
    QColorDialog, QMenu
)


# ──────────────────────────────────────────────────────────────────────────────

RUNNER = str(config.APP_RUNNER)


def launch_app(slug: str):
    """Launch a generator as a completely independent subprocess."""
    proc = QProcess()
    proc.setProgram(sys.executable)
    proc.setArguments([RUNNER, slug, "--root", str(config.APP_ROOT)])
    proc.startDetached()  # detached = truly independent, launcher can close


def make_initials_icon(name: str, color: str, size: int = 64) -> QPixmap:
    """Generate a simple colored circle with initials as a fallback icon."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(color)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.setPen(QPen(QColor("#ffffff")))
    font = QFont("Segoe UI", size // 3, QFont.Weight.Bold)
    painter.setFont(font)
    initials = "".join(w[0].upper() for w in name.split("-")[:2]) or name[0].upper()
    painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, initials[:2])
    painter.end()
    return pm


# ─── App Card ─────────────────────────────────────────────────────────────────

class AppCard(QFrame):
    """A clickable card representing one generator."""

    def __init__(self, slug: str, parent=None):
        super().__init__(parent)
        self.slug = slug
        self.meta = config.read_meta(slug)
        self._running = False

        self.setObjectName("appCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(150, 170)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 10)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # Icon
        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setFixedSize(64, 64)
        self._set_icon()
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignHCenter)

        # Name
        name_label = QLabel(self.meta.get("name", slug))
        name_label.setObjectName("cardName")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setWordWrap(True)
        name_label.setMaximumWidth(130)
        layout.addWidget(name_label)

        # Description (truncated)
        desc = self.meta.get("description", "")
        if desc:
            desc_label = QLabel(desc[:60] + ("…" if len(desc) > 60 else ""))
            desc_label.setObjectName("cardDesc")
            desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            desc_label.setWordWrap(True)
            layout.addWidget(desc_label)

        layout.addStretch()

    def _set_icon(self):
        fav = config.gen_dir(self.slug) / "favicon.png"
        color = self.meta.get("color", "#01696f")
        name  = self.meta.get("name", self.slug)
        if fav.exists():
            pm = QPixmap(str(fav)).scaled(
                64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        else:
            pm = make_initials_icon(name, color, 64)
        self.icon_label.setPixmap(pm)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._launch()
        elif e.button() == Qt.MouseButton.RightButton:
            self._context_menu(e.globalPosition().toPoint())

    def _launch(self):
        launch_app(self.slug)
        # Brief visual flash to indicate launch
        self.setObjectName("appCardLaunching")
        self.style().unpolish(self)
        self.style().polish(self)
        QTimer.singleShot(600, self._reset_style)

    def _reset_style(self):
        self.setObjectName("appCard")
        self.style().unpolish(self)
        self.style().polish(self)

    def _context_menu(self, pos: QPoint):
        menu = QMenu(self)
        menu.addAction("▶  Launch", self._launch)
        menu.addSeparator()
        menu.addAction("✎  Edit App", self._edit)
        menu.addAction("📂  Open Data Folder", self._open_data)
        menu.addAction("🖥  Create Desktop Shortcut", self._create_shortcut)
        menu.addSeparator()
        menu.addAction("🗑  Remove App", self._remove)
        menu.exec(pos)

    def _edit(self):
        dlg = EditAppDialog(self.slug, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.meta = config.read_meta(self.slug)
            self._set_icon()

    def _open_data(self):
        path = config.gen_data_dir(self.slug)
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    def _create_shortcut(self):
        create_desktop_shortcut(self.slug)

    def _remove(self):
        reply = QMessageBox.question(
            self, "Remove App",
            f"Remove '{self.meta.get('name', self.slug)}'?\n\n"
            "This deletes the generator folder but keeps your data (cache/storage/files).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            shutil.rmtree(config.gen_dir(self.slug), ignore_errors=True)
            # Notify parent to refresh
            p = self.parent()
            while p and not isinstance(p, MainWindow):
                p = p.parent()
            if p:
                p.refresh_grid()


# ─── Desktop Shortcut Creator ─────────────────────────────────────────────────

def create_desktop_shortcut(slug: str):
    meta = config.read_meta(slug)
    name = meta.get("name", slug)
    fav  = config.gen_dir(slug) / "favicon.png"
    python = sys.executable
    runner = str(config.APP_RUNNER)
    root   = str(config.APP_ROOT)

    if sys.platform == "win32":
        # Create a .bat launcher + optional .lnk via PowerShell
        desktop = Path(os.path.expanduser("~")) / "Desktop"
        bat_path = desktop / f"{name}.bat"
        bat_path.write_text(
            f'@echo off\n"{python}" "{runner}" {slug} --root "{root}"\n',
            encoding="utf-8"
        )
        # Try to also make a proper .lnk shortcut
        try:
            import winshell
            from win32com.client import Dispatch
            lnk_path = str(desktop / f"{name}.lnk")
            shell = Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(lnk_path)
            shortcut.Targetpath = python
            shortcut.Arguments = f'"{runner}" {slug} --root "{root}"'
            shortcut.WorkingDirectory = root
            if fav.exists():
                shortcut.IconLocation = str(fav)
            shortcut.save()
            bat_path.unlink(missing_ok=True)  # prefer .lnk if it worked
            msg = f"Shortcut created:\n{lnk_path}"
        except Exception:
            msg = f"Batch launcher created:\n{bat_path}"

    elif sys.platform == "darwin":
        # Create a .command file on the Desktop
        desktop = Path(os.path.expanduser("~")) / "Desktop"
        cmd_path = desktop / f"{name}.command"
        cmd_path.write_text(
            f'#!/bin/bash\n"{python}" "{runner}" {slug} --root "{root}"\n',
            encoding="utf-8"
        )
        os.chmod(cmd_path, 0o755)
        msg = f"Launcher created:\n{cmd_path}"

    else:
        # Linux: .desktop file
        desktop = Path(os.path.expanduser("~")) / "Desktop"
        apps_dir = Path(os.path.expanduser("~")) / ".local/share/applications"
        desktop.mkdir(exist_ok=True)
        apps_dir.mkdir(parents=True, exist_ok=True)

        icon_str = str(fav) if fav.exists() else "application-x-executable"
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={name}\n"
            f"Exec={python} {runner} {slug} --root {root}\n"
            f"Icon={icon_str}\n"
            f"Comment=Perchance generator: {slug}\n"
            "Categories=Game;Utility;\n"
            "Terminal=false\n"
            f"StartupWMClass=perchance_{slug}\n"
        )
        for dest in (desktop / f"{name}.desktop", apps_dir / f"perchance-{slug}.desktop"):
            dest.write_text(content, encoding="utf-8")
            os.chmod(dest, 0o755)
        msg = f"Desktop entry created:\n{desktop / (name + '.desktop')}"

    QMessageBox.information(None, "Shortcut Created", msg)


# ─── Add App Dialog ───────────────────────────────────────────────────────────

class AddAppDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Generator App")
        self.setMinimumWidth(420)
        layout = QFormLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        info = QLabel(
            "Enter the perchance.org generator slug.\n"
            "Example: for perchance.org/ai-character-generator\n"
            "enter:  ai-character-generator"
        )
        info.setWordWrap(True)
        info.setObjectName("infoLabel")
        layout.addRow(info)

        self.slug_input = QLineEdit()
        self.slug_input.setPlaceholderText("generator-slug")
        layout.addRow("Generator slug:", self.slug_input)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Display name (optional)")
        layout.addRow("App name:", self.name_input)

        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("Short description (optional)")
        layout.addRow("Description:", self.desc_input)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _validate(self):
        slug = self.slug_input.text().strip().lower().replace(" ", "-")
        if not slug:
            QMessageBox.warning(self, "Invalid", "Slug cannot be empty.")
            return
        if not re.match(r'^[a-z0-9\-_]+$', slug):
            QMessageBox.warning(self, "Invalid",
                "Slug must be lowercase letters, numbers, hyphens, or underscores.")
            return
        self.slug_input.setText(slug)
        self.accept()

    def result_data(self):
        slug = self.slug_input.text().strip()
        name = self.name_input.text().strip() or slug
        desc = self.desc_input.text().strip()
        return slug, name, desc


# ─── Edit App Dialog ──────────────────────────────────────────────────────────

class EditAppDialog(QDialog):
    def __init__(self, slug: str, parent=None):
        super().__init__(parent)
        self.slug = slug
        self.meta = config.read_meta(slug)
        self.setWindowTitle(f"Edit — {slug}")
        self.setMinimumSize(640, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Meta fields
        form = QFormLayout()
        form.setSpacing(10)

        self.name_input = QLineEdit(self.meta.get("name", slug))
        self.desc_input = QLineEdit(self.meta.get("description", ""))
        self.color_btn  = QPushButton()
        self._accent    = self.meta.get("color", "#01696f")
        self._update_color_btn()
        self.color_btn.clicked.connect(self._pick_color)

        form.addRow("App name:", self.name_input)
        form.addRow("Description:", self.desc_input)
        form.addRow("Accent color:", self.color_btn)

        # Favicon
        fav_row = QHBoxLayout()
        fav_label = QLabel("Favicon:")
        self.fav_preview = QLabel()
        self.fav_preview.setFixedSize(40, 40)
        self._refresh_fav_preview()
        fav_btn = QPushButton("Choose image…")
        fav_btn.setObjectName("secondaryBtn")
        fav_btn.clicked.connect(self._pick_fav)
        fav_row.addWidget(fav_label)
        fav_row.addWidget(self.fav_preview)
        fav_row.addWidget(fav_btn)
        fav_row.addStretch()
        form.addRow(fav_row)

        layout.addLayout(form)

        # overrides.js editor
        layout.addWidget(QLabel("overrides.js  (runs after global-overrides.js on every page load)"))
        self.js_editor = QTextEdit()
        self.js_editor.setFont(QFont("Consolas", 11))
        js_file = config.gen_dir(slug) / "overrides.js"
        if js_file.exists():
            self.js_editor.setPlainText(js_file.read_text("utf-8"))
        layout.addWidget(self.js_editor, 1)

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Save).setText("Save")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._accent), self, "Choose accent color")
        if c.isValid():
            self._accent = c.name()
            self._update_color_btn()

    def _update_color_btn(self):
        self.color_btn.setText(self._accent)
        self.color_btn.setStyleSheet(
            f"background:{self._accent}; color:#fff; border-radius:5px; padding:4px 12px;"
        )

    def _pick_fav(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Favicon", "", "Images (*.png *.jpg *.ico *.svg *.webp)"
        )
        if path:
            dest = config.gen_dir(self.slug) / "favicon.png"
            shutil.copy(path, dest)
            self._refresh_fav_preview()

    def _refresh_fav_preview(self):
        fav = config.gen_dir(self.slug) / "favicon.png"
        if fav.exists():
            pm = QPixmap(str(fav)).scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.fav_preview.setPixmap(pm)
        else:
            self.fav_preview.setText("—")

    def _save(self):
        meta = config.read_meta(self.slug)
        meta["name"]        = self.name_input.text().strip() or self.slug
        meta["description"] = self.desc_input.text().strip()
        meta["color"]       = self._accent
        config.write_meta(self.slug, meta)

        js_file = config.gen_dir(self.slug) / "overrides.js"
        js_file.write_text(self.js_editor.toPlainText(), encoding="utf-8")
        self.accept()


# ─── Main Launcher Window ─────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Perchance App Engine")
        self.setMinimumSize(700, 500)
        self.resize(900, 600)
        fav = config.ASSETS_DIR / "launcher-icon.png"
        if fav.exists():
            self.setWindowIcon(QIcon(str(fav)))

        self._build_ui()
        self._apply_theme()
        self.refresh_grid()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Header ──
        header = QFrame()
        header.setObjectName("launcherHeader")
        header.setFixedHeight(60)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(20, 0, 16, 0)

        title = QLabel("⚙ Perchance Engine")
        title.setObjectName("launcherTitle")
        h_layout.addWidget(title)
        h_layout.addStretch()

        # Search
        self.search = QLineEdit()
        self.search.setObjectName("searchBar")
        self.search.setPlaceholderText("Search apps…")
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(self.refresh_grid)
        h_layout.addWidget(self.search)

        # Add app button
        add_btn = QPushButton("＋  Add App")
        add_btn.setObjectName("addBtn")
        add_btn.setFixedHeight(34)
        add_btn.clicked.connect(self._add_app)
        h_layout.addWidget(add_btn)

        # Edit global overrides
        global_btn = QPushButton("✎ Global JS")
        global_btn.setObjectName("secondaryBtn")
        global_btn.setFixedHeight(34)
        global_btn.clicked.connect(self._edit_global)
        h_layout.addWidget(global_btn)

        root_layout.addWidget(header)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setObjectName("headerDivider")
        root_layout.addWidget(div)

        # ── Scrollable grid ──
        scroll = QScrollArea()
        scroll.setObjectName("appScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.grid_container = QWidget()
        self.grid_container.setObjectName("gridContainer")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(20, 20, 20, 20)
        self.grid_layout.setSpacing(14)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        scroll.setWidget(self.grid_container)
        root_layout.addWidget(scroll, 1)

        # ── Footer ──
        footer = QFrame()
        footer.setObjectName("footer")
        footer.setFixedHeight(28)
        f_layout = QHBoxLayout(footer)
        f_layout.setContentsMargins(16, 0, 16, 0)
        self.footer_label = QLabel()
        self.footer_label.setObjectName("footerLabel")
        f_layout.addWidget(self.footer_label)
        f_layout.addStretch()

        open_data_btn = QPushButton("📂 Open Data Folder")
        open_data_btn.setObjectName("footerBtn")
        open_data_btn.clicked.connect(self._open_root_data)
        f_layout.addWidget(open_data_btn)

        root_layout.addWidget(footer)

    def refresh_grid(self):
        # Clear existing cards
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        query = self.search.text().strip().lower()
        gens = [
            g for g in config.list_generators()
            if not query or query in g.lower() or
               query in config.read_meta(g).get("name", "").lower()
        ]

        if not gens:
            empty = QLabel(
                "No apps yet.\nClick  ＋ Add App  to install a generator." if not query
                else f"No apps matching \"{query}\""
            )
            empty.setObjectName("emptyLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid_layout.addWidget(empty, 0, 0, Qt.AlignmentFlag.AlignCenter)
        else:
            cols = max(1, (self.width() - 40) // 164)
            for i, slug in enumerate(gens):
                card = AppCard(slug, self)
                self.grid_layout.addWidget(card, i // cols, i % cols)

        count = len(gens)
        self.footer_label.setText(
            f"{count} app{'s' if count != 1 else ''} installed"
        )

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self.refresh_grid)

    def _add_app(self):
        dlg = AddAppDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        slug, name, desc = dlg.result_data()

        gdir = config.gen_dir(slug)
        gdir.mkdir(parents=True, exist_ok=True)

        # Scaffold files
        js_file = gdir / "overrides.js"
        if not js_file.exists():
            js_file.write_text(
                f"// overrides.js — injected only when '{slug}' loads\n"
                f"console.log('[PE] {slug} loaded.');\n",
                encoding="utf-8"
            )

        config.write_meta(slug, {
            "name": name,
            "slug": slug,
            "description": desc,
            "url": f"https://perchance.org/{slug}",
            "color": "#01696f",
            "version": "1.0",
        })

        self.refresh_grid()

        # Offer to launch immediately
        reply = QMessageBox.question(
            self, "Launch App",
            f"'{name}' was added. Launch it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            launch_app(slug)

    def _edit_global(self):
        dlg = _EditFileDialog("global-overrides.js", config.GLOBAL_JS, self)
        dlg.exec()

    def _open_root_data(self):
        path = config.DATA_DIR
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background:#1c1b19; color:#cdccca;
                font-family:'Segoe UI','Inter',sans-serif; font-size:13px;
            }
            #launcherHeader { background:#171614; border-bottom:none; }
            #launcherTitle { font-size:17px; font-weight:700; color:#4f98a3;
                letter-spacing:0.5px; }
            #headerDivider { background:#262523; border:none; max-height:1px; }
            #searchBar {
                background:#22211f; border:1px solid #393836; border-radius:17px;
                padding:5px 14px; color:#cdccca; font-size:12px;
            }
            #searchBar:focus { border-color:#4f98a3; }
            #addBtn {
                background:#01696f; border:none; border-radius:7px;
                padding:0 16px; color:#f9f8f5; font-weight:600; font-size:13px;
            }
            #addBtn:hover { background:#0c4e54; }
            #addBtn:pressed { background:#0f3638; }
            #secondaryBtn {
                background:#22211f; border:1px solid #393836; border-radius:7px;
                padding:0 12px; color:#9a9896; font-size:12px;
            }
            #secondaryBtn:hover { background:#2d2c2a; color:#cdccca;
                border-color:#5a5957; }
            #appScroll { background:#1c1b19; border:none; }
            #gridContainer { background:#1c1b19; }
            #appCard {
                background:#201f1d; border:1px solid #2d2c2a; border-radius:12px;
            }
            #appCard:hover {
                background:#252422; border-color:#393836;
            }
            #appCardLaunching {
                background:#313b3b; border:1px solid #4f98a3; border-radius:12px;
            }
            #cardName { font-size:12px; font-weight:600; color:#cdccca; }
            #cardDesc { font-size:10px; color:#797876; }
            #emptyLabel { font-size:14px; color:#5a5957; }
            #footer { background:#171614; border-top:1px solid #1f1e1c; }
            #footerLabel { font-size:11px; color:#5a5957; }
            #footerBtn {
                background:transparent; border:none; color:#5a5957; font-size:11px;
            }
            #footerBtn:hover { color:#9a9896; }
            QScrollBar:vertical { background:#1c1b19; width:5px; }
            QScrollBar::handle:vertical { background:#393836; border-radius:2px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            QDialog { background:#1c1b19; }
            QLabel { color:#cdccca; }
            QLineEdit, QTextEdit {
                background:#22211f; border:1px solid #393836; border-radius:6px;
                padding:6px 10px; color:#cdccca; selection-background-color:#313b3b;
            }
            QLineEdit:focus, QTextEdit:focus { border-color:#4f98a3; }
            QDialogButtonBox QPushButton {
                background:#22211f; border:1px solid #393836; border-radius:6px;
                padding:6px 16px; color:#cdccca; min-width:80px;
            }
            QDialogButtonBox QPushButton:hover {
                background:#313b3b; border-color:#4f98a3; color:#4f98a3;
            }
            QFormLayout QLabel { color:#9a9896; font-size:12px; }
            #infoLabel { color:#797876; font-size:11px; }
        """)


class _EditFileDialog(QDialog):
    def __init__(self, title: str, filepath: Path, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.setWindowTitle(f"Edit — {title}")
        self.setMinimumSize(680, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel(str(filepath)))
        self.editor = QTextEdit()
        self.editor.setFont(QFont("Consolas", 11))
        if filepath.exists():
            self.editor.setPlainText(filepath.read_text("utf-8"))
        layout.addWidget(self.editor, 1)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Save).setText("Save")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _save(self):
        self.filepath.write_text(self.editor.toPlainText(), encoding="utf-8")
        self.accept()

# ──────────────────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Perchance Engine")
    app.setOrganizationName("PerchanceEngine")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()