#!/usr/bin/env python3
"""
launcher.py — Jawless Perchance App Engine Launcher
Displays all installed generators as app cards; launches each in its own
isolated subprocess (separate window, profile, cache, and storage).
"""

import sys
import os
import shutil
import re
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))
import config
try:
    from configurate import python_exe as configured_python_exe
except Exception:  # launcher remains usable if the optional settings UI is absent
    def configured_python_exe():
        return sys.executable

from PyQt6.QtCore import Qt, QSize, QProcess, QTimer, QPoint
from PyQt6.QtGui import (
    QIcon, QPixmap, QColor, QFont, QPainter, QBrush, QPen,
    QKeySequence, QShortcut
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel,
    QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea,
    QLineEdit, QPushButton, QDialog, QDialogButtonBox,
    QFormLayout, QTextEdit, QFileDialog, QMessageBox,
    QColorDialog, QMenu
)


# ──────────────────────────────────────────────────────────────────────────────

RUNNER = str(config.APP_RUNNER)

# Characters Windows/macOS refuse in filenames
_BAD_FILENAME_CHARS = r'<>:"/\|?*'


# ─── Small color helpers (used to derive accent shades) ──────────────────────

def _accent(meta: dict) -> str:
    """Validated accent color for a generator, falling back to the default."""
    c = QColor(str(meta.get("color", "") or ""))
    return c.name() if c.isValid() else "#01696f"


def _mix(color: str, other: str, ratio: float) -> str:
    """Blend `color` into `other` by ratio (0 = other, 1 = color)."""
    a, b = QColor(color), QColor(other)
    if not a.isValid():
        return other
    r = max(0.0, min(1.0, ratio))
    return QColor(
        round(b.red()   + (a.red()   - b.red())   * r),
        round(b.green() + (a.green() - b.green()) * r),
        round(b.blue()  + (a.blue()  - b.blue())  * r),
    ).name()


def _readable_on(color: str) -> str:
    """Black or white text, whichever reads better on `color`."""
    c = QColor(color)
    if not c.isValid():
        return "#ffffff"
    lum = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255
    return "#151413" if lum > 0.6 else "#ffffff"


def safe_filename(name: str, fallback: str = "app") -> str:
    """Strip characters that are illegal in filenames on Windows/macOS."""
    cleaned = "".join("_" if ch in _BAD_FILENAME_CHARS else ch for ch in str(name))
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    cleaned = cleaned.strip().strip(".")          # Windows hates trailing dots/spaces
    # reserved DOS device names
    if cleaned.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        cleaned = f"_{cleaned}"
    return cleaned[:120] or fallback


# Companion tools that live beside launcher.py in the program root.
# (filename, button label, icon, tooltip, keyboard shortcut)
TOOLS = [
    ("gallery.py",     "Gallery",   "\U0001f5bc",
     "Browse, tag and search every generated image", "Ctrl+G"),
    ("twnotes.py",     "Notes",     "\U0001f4dd",
     "Open your TiddlyWiki notes",                   "Ctrl+N"),
    ("configurate.py", "Configure", "\u2699",
     "Open engine configuration",                    "Ctrl+,"),
]


def launch_app(slug: str):
    """Launch a generator as a completely independent subprocess."""
    proc = QProcess()
    proc.setProgram(configured_python_exe())
    proc.setArguments([RUNNER, slug, "--root", str(config.APP_ROOT)])
    proc.setWorkingDirectory(str(config.APP_ROOT))
    proc.startDetached()  # detached = truly independent, launcher can close


def tool_path(filename: str) -> Path:
    """Companion scripts sit in the program root, next to config.py."""
    return config.APP_ROOT / filename


def launch_tool(filename: str, label: str = "", parent=None) -> bool:
    """Run one of the companion scripts in its own detached process."""
    path = tool_path(filename)
    label = label or filename
    if not path.exists():
        QMessageBox.warning(
            parent, f"{label} not found",
            f"Could not find {filename}.\n\nExpected it here:\n{path}"
        )
        return False

    proc = QProcess()
    proc.setProgram(configured_python_exe())
    proc.setArguments([str(path)])
    proc.setWorkingDirectory(str(config.APP_ROOT))
    started, _pid = proc.startDetached()
    if not started:
        QMessageBox.critical(
            parent, f"Could not open {label}",
            f"Failed to start:\n{configured_python_exe()} {path}"
        )
    return started


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
        self.setProperty("launching", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(150, 170)

        # outer layout has no padding so the accent strip can span the full
        # width of the card; all content lives in a padded inner layout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        content = QWidget()
        content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        outer.addWidget(content, 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(9, 11, 9, 4)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # Icon (sits on an accent-tinted backdrop)
        self.icon_holder = QFrame()
        self.icon_holder.setObjectName("cardIconHolder")
        self.icon_holder.setFixedSize(72, 72)
        ih = QVBoxLayout(self.icon_holder)
        ih.setContentsMargins(4, 4, 4, 4)
        self.icon_label = QLabel()
        self.icon_label.setObjectName("cardIcon")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setFixedSize(64, 64)
        ih.addWidget(self.icon_label)
        layout.addWidget(self.icon_holder, 0, Qt.AlignmentFlag.AlignHCenter)

        # Name
        self.name_label = QLabel(self.meta.get("name", slug))
        self.name_label.setObjectName("cardName")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setWordWrap(True)
        self.name_label.setMaximumWidth(130)
        layout.addWidget(self.name_label)

        # Description (truncated)
        self.desc_label = QLabel()
        self.desc_label.setObjectName("cardDesc")
        self.desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label)

        layout.addStretch()

        # Accent strip along the bottom edge of the card
        self.accent_bar = QFrame()
        self.accent_bar.setObjectName("cardAccentBar")
        self.accent_bar.setFixedHeight(4)
        outer.addWidget(self.accent_bar)

        self._apply_meta()

    # -- meta / accent ----------------------------------------------------
    def reload_meta(self):
        """Re-read meta.json and repaint the card (icon, accent, text)."""
        self.meta = config.read_meta(self.slug)
        self._apply_meta()

    def _apply_meta(self):
        self.name_label.setText(self.meta.get("name", self.slug))
        desc = self.meta.get("description", "") or ""
        self.desc_label.setText(desc[:60] + ("…" if len(desc) > 60 else ""))
        self.desc_label.setVisible(bool(desc))
        self.setToolTip(
            f"{self.meta.get('name', self.slug)}\n{self.slug}"
            + (f"\n\n{desc}" if desc else "")
        )
        self._set_icon()
        self.apply_accent()

    def apply_accent(self):
        """Paint this card with the generator's accent color.

        The global theme only sets neutral defaults; this per-card stylesheet
        is what actually makes the 'Accent color' setting visible."""
        accent = _accent(self.meta)
        base = "#201f1d"
        hover_bg = _mix(accent, base, 0.14)
        launch_bg = _mix(accent, base, 0.30)
        border = _mix(accent, "#2d2c2a", 0.35)
        icon_bg = _mix(accent, base, 0.18)
        self.setStyleSheet(f"""
            QFrame#appCard {{
                background:{base}; border:1px solid {border}; border-radius:12px;
            }}
            QFrame#appCard:hover {{
                background:{hover_bg}; border:1px solid {accent};
            }}
            QFrame#appCard[launching="true"] {{
                background:{launch_bg}; border:1px solid {accent};
            }}
            QFrame#cardIconHolder {{
                background:{icon_bg}; border:1px solid {_mix(accent, base, 0.45)};
                border-radius:16px;
            }}
            QFrame#appCard > QWidget {{ background:transparent; border:none; }}
            QLabel#cardIcon {{ background:transparent; border:none; }}
            QLabel#cardName {{ font-size:12px; font-weight:600; color:#e3e2e0;
                background:transparent; }}
            QLabel#cardDesc {{ font-size:10px; color:#8b8a88; background:transparent; }}
        """)
        # set on the bar itself so the card's "transparent children" rule
        # cannot win over it
        self.accent_bar.setStyleSheet(
            f"background:{accent}; border:none;"
            "border-bottom-left-radius:11px; border-bottom-right-radius:11px;"
        )

    def _set_icon(self):
        fav = config.gen_dir(self.slug) / "favicon.png"
        color = _accent(self.meta)
        name  = self.meta.get("name", self.slug)
        pm = None
        if fav.exists():
            loaded = QPixmap(str(fav))
            if not loaded.isNull():
                pm = loaded.scaled(
                    64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
        if pm is None:
            pm = make_initials_icon(name, color, 64)
        self.icon_label.setPixmap(pm)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._launch()
        elif e.button() == Qt.MouseButton.RightButton:
            self._context_menu(e.globalPosition().toPoint())

    def _launch(self):
        launch_app(self.slug)
        # Brief visual flash to indicate launch (accent-tinted)
        self._set_launching(True)
        QTimer.singleShot(600, lambda: self._set_launching(False))

    def _set_launching(self, on: bool):
        # dynamic property keeps objectName (and therefore :hover) intact
        self.setProperty("launching", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

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
            self.reload_meta()

    def _open_data(self):
        path = config.gen_data_dir(self.slug)
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    def _create_shortcut(self):
        create_desktop_shortcut(self.slug, parent=self)

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

def _windows_desktop_from_registry():
    """Ask Windows where the Desktop actually is.

    With OneDrive 'Back up your folders' enabled, the Desktop is redirected to
    %USERPROFILE%\\OneDrive\\Desktop, so the hard-coded ~/Desktop does not exist
    — that is what produced the FileNotFoundError."""
    # 1) SHGetKnownFolderPath — the authoritative answer
    try:
        import ctypes
        from ctypes import wintypes, windll, byref, c_wchar_p

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

        # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
        desktop_id = GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                          (wintypes.BYTE * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                              0x9A, 0x87, 0xC6, 0x41))
        path_ptr = c_wchar_p()
        if windll.shell32.SHGetKnownFolderPath(
                byref(desktop_id), 0, None, byref(path_ptr)) == 0:
            value = path_ptr.value
            windll.ole32.CoTaskMemFree(path_ptr)
            if value:
                p = Path(value)
                if p.is_dir():
                    return p
    except Exception:
        pass

    # 2) Registry shell folders (expands %USERPROFILE% style values)
    try:
        import winreg
        for hive, key in [
            (winreg.HKEY_CURRENT_USER,
             r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"),
            (winreg.HKEY_CURRENT_USER,
             r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"),
        ]:
            try:
                with winreg.OpenKey(hive, key) as k:
                    raw, _ = winreg.QueryValueEx(k, "Desktop")
                p = Path(os.path.expandvars(raw))
                if p.is_dir():
                    return p
            except OSError:
                continue
    except Exception:
        pass
    return None


def get_desktop_dir() -> Path:
    """Best-effort Desktop directory, created if it is genuinely missing."""
    home = Path(os.path.expanduser("~"))

    if sys.platform == "win32":
        found = _windows_desktop_from_registry()
        if found:
            return found

    candidates = [home / "Desktop"]
    if sys.platform == "win32":
        candidates.append(home / "OneDrive" / "Desktop")
        # OneDrive for Business folders look like "OneDrive - Company"
        try:
            candidates.extend(sorted(
                p / "Desktop" for p in home.glob("OneDrive*") if p.is_dir()
            ))
        except OSError:
            pass
        for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            val = os.environ.get(var)
            if val:
                candidates.append(Path(val) / "Desktop")
    else:
        # honour XDG on Linux
        xdg = os.environ.get("XDG_DESKTOP_DIR")
        if xdg:
            candidates.insert(0, Path(os.path.expandvars(xdg)))

    for c in candidates:
        try:
            if c.is_dir():
                return c
        except OSError:
            continue

    # Nothing exists — create the conventional one rather than crashing
    fallback = candidates[0]
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    except OSError:
        return home


def _pythonw(python: str) -> str:
    """On Windows prefer pythonw.exe so no console window flashes up."""
    if sys.platform != "win32":
        return python
    p = Path(python)
    if p.name.lower() == "python.exe":
        cand = p.with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    return python


def create_desktop_shortcut(slug: str, parent=None):
    meta = config.read_meta(slug)
    name = safe_filename(meta.get("name", slug) or slug, fallback=slug)
    fav  = config.gen_dir(slug) / "favicon.png"
    python = configured_python_exe()
    runner = str(config.APP_RUNNER)
    root   = str(config.APP_ROOT)

    try:
        desktop = get_desktop_dir()
        desktop.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        QMessageBox.critical(parent, "Shortcut Failed",
                             f"Could not find or create your Desktop folder.\n\n{exc}")
        return

    try:
        msg = _write_shortcut(desktop, slug, name, fav, python, runner, root)
    except Exception as exc:
        QMessageBox.critical(
            parent, "Shortcut Failed",
            f"Could not create the shortcut.\n\n"
            f"Target folder: {desktop}\n{type(exc).__name__}: {exc}"
        )
        return

    QMessageBox.information(parent, "Shortcut Created", msg)


def _write_shortcut(desktop: Path, slug, name, fav, python, runner, root) -> str:
    if sys.platform == "win32":
        gui_python = _pythonw(python)

        # Try a proper .lnk first (needs pywin32); fall back to a .bat
        try:
            from win32com.client import Dispatch   # noqa: F401  (pywin32)
            lnk_path = desktop / f"{name}.lnk"
            shell = Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(str(lnk_path))
            shortcut.Targetpath = gui_python
            shortcut.Arguments = f'"{runner}" {slug} --root "{root}"'
            shortcut.WorkingDirectory = root
            shortcut.Description = f"Perchance generator: {slug}"
            if fav.exists():
                # .lnk icons want .ico/.exe; convert the favicon once
                ico = _ensure_ico(fav)
                if ico:
                    shortcut.IconLocation = str(ico)
            shortcut.save()
            return f"Shortcut created:\n{lnk_path}"
        except Exception:
            pass

        bat_path = desktop / f"{name}.bat"
        bat_path.write_text(
            "@echo off\r\n"
            f'cd /d "{root}"\r\n'
            f'start "" "{gui_python}" "{runner}" {slug} --root "{root}"\r\n',
            encoding="utf-8"
        )
        return (f"Batch launcher created:\n{bat_path}\n\n"
                "Install pywin32 (pip install pywin32) to get a real .lnk "
                "shortcut with the app icon instead.")

    elif sys.platform == "darwin":
        cmd_path = desktop / f"{name}.command"
        cmd_path.write_text(
            f'#!/bin/bash\ncd "{root}"\nexec "{python}" "{runner}" {slug} --root "{root}"\n',
            encoding="utf-8"
        )
        os.chmod(cmd_path, 0o755)
        return f"Launcher created:\n{cmd_path}"

    else:
        # Linux: .desktop file
        apps_dir = Path(os.path.expanduser("~")) / ".local/share/applications"
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
        return f"Desktop entry created:\n{desktop / (name + '.desktop')}"


def _ensure_ico(fav: Path):
    """Windows .lnk icons need an .ico — build one next to the favicon."""
    ico = fav.with_suffix(".ico")
    try:
        if ico.exists() and ico.stat().st_mtime >= fav.stat().st_mtime:
            return ico
        pm = QPixmap(str(fav))
        if pm.isNull():
            return None
        pm.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio,
                  Qt.TransformationMode.SmoothTransformation).save(str(ico), "ICO")
        return ico if ico.exists() else None
    except Exception:
        return None


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
        self._accent    = _accent(self.meta)

        # Accent row: swatch button + editable hex + live card preview
        self.color_btn = QPushButton("Pick…")
        self.color_btn.clicked.connect(self._pick_color)
        self.hex_input = QLineEdit(self._accent)
        self.hex_input.setFixedWidth(90)
        self.hex_input.setPlaceholderText("#rrggbb")
        self.hex_input.textEdited.connect(self._hex_typed)
        reset_btn = QPushButton("Reset")
        reset_btn.setObjectName("secondaryBtn")
        reset_btn.clicked.connect(lambda: self._set_accent("#01696f"))
        self.accent_preview = QLabel("  preview  ")
        self.accent_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.accent_preview.setFixedHeight(26)

        color_row = QHBoxLayout()
        color_row.setSpacing(8)
        color_row.addWidget(self.color_btn)
        color_row.addWidget(self.hex_input)
        color_row.addWidget(reset_btn)
        color_row.addWidget(self.accent_preview, 1)
        color_wrap = QWidget()
        color_wrap.setLayout(color_row)

        form.addRow("App name:", self.name_input)
        form.addRow("Description:", self.desc_input)
        form.addRow("Accent color:", color_wrap)
        self._update_color_btn()

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
            self._set_accent(c.name())

    def _hex_typed(self, text):
        c = QColor(text.strip())
        if c.isValid():
            self._accent = c.name()
            self._update_color_btn(sync_field=False)

    def _set_accent(self, value: str):
        c = QColor(value)
        self._accent = c.name() if c.isValid() else "#01696f"
        self._update_color_btn()

    def _update_color_btn(self, sync_field: bool = True):
        if sync_field and self.hex_input.text().strip().lower() != self._accent:
            self.hex_input.setText(self._accent)
        fg = _readable_on(self._accent)
        self.color_btn.setStyleSheet(
            f"background:{self._accent}; color:{fg}; border:none; border-radius:5px;"
            f"padding:5px 14px; font-weight:600;"
        )
        self.accent_preview.setStyleSheet(
            f"background:{_mix(self._accent, '#201f1d', 0.18)};"
            f"border:1px solid {self._accent}; border-radius:6px; color:#cdccca;"
        )
        # keep the initials icon preview honest when there is no favicon
        if hasattr(self, "fav_preview") and \
                not (config.gen_dir(self.slug) / "favicon.png").exists():
            self.fav_preview.setPixmap(make_initials_icon(
                self.name_input.text().strip() or self.slug, self._accent, 40))

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
            self.fav_preview.setPixmap(make_initials_icon(
                self.meta.get("name", self.slug), _accent(self.meta), 40))

    def _save(self):
        meta = config.read_meta(self.slug)
        meta["name"]        = self.name_input.text().strip() or self.slug
        meta["description"] = self.desc_input.text().strip()
        meta["color"]       = _accent({"color": self._accent})
        config.write_meta(self.slug, meta)

        js_file = config.gen_dir(self.slug) / "overrides.js"
        js_file.write_text(self.js_editor.toPlainText(), encoding="utf-8")
        self.accept()


# ─── Main Launcher Window ─────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Jawless Perchance App Engine")
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

        title = QLabel("⚙ Jawless Perchance Engine")
        title.setObjectName("launcherTitle")
        h_layout.addWidget(title)
        h_layout.addStretch()

        # ── Companion tool buttons (gallery / notes / configurate) ──
        self.tool_buttons = {}
        for filename, label, icon, tip, keyseq in TOOLS:
            btn = QPushButton(f"{icon}  {label}")
            btn.setObjectName("toolBtn")
            btn.setFixedHeight(34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            exists = tool_path(filename).exists()
            btn.setToolTip(
                f"{tip}   ({keyseq})" if exists
                else f"{filename} is missing from {config.APP_ROOT}"
            )
            btn.setProperty("missing", not exists)
            btn.clicked.connect(
                lambda _=False, f=filename, l=label: launch_tool(f, l, self))
            QShortcut(QKeySequence(keyseq), self,
                      lambda f=filename, l=label: launch_tool(f, l, self))
            self.tool_buttons[filename] = btn
            h_layout.addWidget(btn)
        self._sync_tool_buttons()

        # separator between tools and app management
        sep = QFrame()
        sep.setObjectName("headerSep")
        sep.setFixedSize(1, 24)
        h_layout.addSpacing(6)
        h_layout.addWidget(sep)
        h_layout.addSpacing(6)

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
            self._cols = cols
            for i, slug in enumerate(gens):
                card = AppCard(slug, self)
                self.grid_layout.addWidget(card, i // cols, i % cols)

        self._sync_tool_buttons()
        count = len(gens)
        self.footer_label.setText(
            f"{count} app{'s' if count != 1 else ''} installed"
        )

    def _sync_tool_buttons(self):
        """Collapse the tool buttons to icons on narrow windows and refresh
        their 'missing file' state (so adding configurate.py later just works)."""
        compact = self.width() < 1010
        for filename, label, icon, tip, keyseq in TOOLS:
            btn = self.tool_buttons.get(filename)
            if btn is None:
                continue
            btn.setText(icon if compact else f"{icon}  {label}")
            if compact:
                btn.setFixedWidth(40)
            else:
                btn.setMinimumWidth(0)
                btn.setMaximumWidth(16777215)
            exists = tool_path(filename).exists()
            btn.setToolTip(
                f"{label} — {tip}   ({keyseq})" if exists
                else f"{filename} is missing from {config.APP_ROOT}"
            )
            if bool(btn.property("missing")) != (not exists):
                btn.setProperty("missing", not exists)
                btn.style().unpolish(btn)
                btn.style().polish(btn)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_tool_buttons()
        # only relayout when the column count actually changes, otherwise every
        # pixel of resize rebuilt (and flickered) the whole card grid
        cols = max(1, (self.width() - 40) // 164)
        if cols != getattr(self, "_cols", None):
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
            #headerSep { background:#2d2c2a; border:none; }
            #toolBtn {
                background:#22211f; border:1px solid #393836; border-radius:7px;
                padding:0 12px; color:#cdccca; font-size:12px;
            }
            #toolBtn:hover { background:#26312f; border-color:#4f98a3;
                color:#7fc3cd; }
            #toolBtn:pressed { background:#1b2422; }
            #toolBtn[missing="true"] { color:#6b6a68; border-color:#2d2c2a; }
            #appScroll { background:#1c1b19; border:none; }
            #gridContainer { background:#1c1b19; }
            #appCard {
                background:#201f1d; border:1px solid #2d2c2a; border-radius:12px;
            }
            #appCard:hover {
                background:#252422; border-color:#393836;
            }
            /* per-card accent styling lives in AppCard.apply_accent();
               these are only the neutral fallbacks */
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
    app.setApplicationName("Jawless Perchance Engine")
    app.setOrganizationName("Jawless")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
