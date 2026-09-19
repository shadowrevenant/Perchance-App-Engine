#!/usr/bin/env python3
"""First-run GPL notice and launcher bootstrap for Jawless."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
)


APP_ROOT = Path(__file__).resolve().parent
LICENSE_FILE = APP_ROOT / "LICENSE"
STATUS_FILE = APP_ROOT / "license_status.cfg"
TARGET_APP = APP_ROOT / "launcher.py"
ACKNOWLEDGEMENT = "GPL_NOTICE_ACKNOWLEDGED=TRUE"


def notice_acknowledged() -> bool:
    try:
        return ACKNOWLEDGEMENT in STATUS_FILE.read_text(encoding="utf-8")
    except OSError:
        return False


def write_acknowledgement() -> None:
    STATUS_FILE.write_text(ACKNOWLEDGEMENT + "\n", encoding="utf-8")


def launch_target_app() -> bool:
    if not TARGET_APP.is_file():
        QMessageBox.critical(
            None,
            "Missing launcher",
            f"Could not find the launcher:\n{TARGET_APP}",
        )
        return False
    try:
        subprocess.Popen([sys.executable, str(TARGET_APP)], cwd=str(APP_ROOT))
        return True
    except OSError as exc:
        QMessageBox.critical(None, "Could not launch", str(exc))
        return False


class LicenseNotice(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Jawless Perchance App Engine — GPLv3 Notice")
        self.resize(760, 620)
        self.setMinimumSize(560, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        heading = QLabel("GNU General Public License version 3")
        heading.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        layout.addWidget(heading)

        explanation = QLabel(
            "Jawless Perchance App Engine is free software distributed under "
            "GPLv3. You may use, study, modify, and redistribute it under that "
            "license. It comes with no warranty. Bundled third-party components "
            "remain governed by their own licenses."
        )
        explanation.setWordWrap(True)
        explanation.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(explanation)

        license_text = QTextEdit()
        license_text.setReadOnly(True)
        try:
            license_text.setPlainText(LICENSE_FILE.read_text(encoding="utf-8"))
        except OSError as exc:
            license_text.setPlainText(f"LICENSE could not be read:\n{exc}")
        license_text.setFont(QFont("Consolas", 9))
        layout.addWidget(license_text, 1)

        self.acknowledge = QCheckBox(
            "I have received the GPLv3 license and understand there is no warranty."
        )
        layout.addWidget(self.acknowledge)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Continue")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Quit")
        self.acknowledge.toggled.connect(
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Jawless Perchance App Engine")
    app.setOrganizationName("Jawless")

    if not LICENSE_FILE.is_file():
        QMessageBox.critical(None, "Missing license", f"Could not find:\n{LICENSE_FILE}")
        return 1

    if notice_acknowledged():
        return 0 if launch_target_app() else 1

    dialog = LicenseNotice()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return 0
    try:
        write_acknowledgement()
    except OSError as exc:
        QMessageBox.warning(
            None,
            "Could not save acknowledgement",
            f"The application can continue, but this notice may appear again.\n\n{exc}",
        )
    return 0 if launch_target_app() else 1


if __name__ == "__main__":
    sys.exit(main())
