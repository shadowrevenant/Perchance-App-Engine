import sys
import os
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QFileDialog
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtCore import QUrl

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
APP_ROOT = Path(__file__).resolve().parent
WIKI_PATH = APP_ROOT / "datastore.html"
# ---------------------------------------------------------

GALLERY_STYLESHEET = """
    QMainWindow, QWidget { 
        background: #1c1b19; 
        color: #cdccca;
        font-family: 'Segoe UI', sans-serif; 
        font-size: 13px; 
    }
    QFileDialog {
        background-color: #1c1b19;
    }
    QLineEdit, QFileDialog QListView {
        background: #22211f; 
        border: 1px solid #393836;
        border-radius: 6px; 
        padding: 5px 10px;
        color: #cdccca;
    }
    QPushButton { 
        background: #22211f; 
        border: 1px solid #393836;
        border-radius: 6px; 
        padding: 5px 12px;
        color: #cdccca;
    }
    QPushButton:hover { 
        border-color: #01696f; 
    }
    QPushButton:pressed { 
        background: #01696f; 
        color: #fff; 
    }
    QScrollBar:vertical { 
        background: #1c1b19; 
        width: 10px; 
    }
    QScrollBar::handle:vertical { 
        background: #3a3936; 
        border-radius: 5px; 
    }
    QScrollBar::handle:vertical:hover { 
        background: #01696f; 
    }
"""

class TiddlyWikiBrowser(QWebEngineView):
    def __init__(self, wiki_path):
        super().__init__()
        self.wiki_file_path = os.path.abspath(wiki_path)
        self.wiki_file_name = os.path.basename(self.wiki_file_path)

        # Apply standard theme styles to browser frames/dialogs
        self.setStyleSheet(GALLERY_STYLESHEET)

        # 1. SETUP DOWNLOAD INTERCEPTION
        self.page().profile().downloadRequested.connect(self.handle_download)

        # 2. ENABLE LOCAL FILE ACCESS (CORS Bypass helpers)
        settings = self.page().settings()
        attribute = QWebEngineSettings.WebAttribute
        # Keep the local wiki isolated from the network. It may read its own
        # local assets and use localStorage, but cannot fetch arbitrary remote
        # content or bridge remote pages to local files.
        settings.setAttribute(attribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(attribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(attribute.LocalStorageEnabled, True)

        # 3. LOAD THE WIKI
        print(f"Loading Wiki from: {self.wiki_file_path}")
        self.load(QUrl.fromLocalFile(self.wiki_file_path))
        self.resize(1200, 900)

    def handle_download(self, download_item):
        """
        Smart download handler:
        - If the file is the wiki itself -> Overwrite silently (Auto-save)
        - If it's an export (json, text, image) -> Show 'Save As' dialog
        """
        suggested_name = download_item.suggestedFileName()
        is_wiki_save = (suggested_name == self.wiki_file_name) or (suggested_name == "index.html")

        if is_wiki_save:
            # --- SCENARIO A: AUTO-SAVE THE WIKI ---
            print(f"Wiki Save Detected ({suggested_name}). Overwriting {self.wiki_file_path}...")
            target = Path(self.wiki_file_path)
            download_item.setDownloadDirectory(str(target.parent))
            download_item.setDownloadFileName(target.name)
            download_item.accept()

        else:
            # --- SCENARIO B: EXPORTING A FILE ---
            print(f"Export Detected ({suggested_name}). Prompting user...")

            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export File",
                os.path.join(os.getcwd(), suggested_name),
                "All Files (*)"
            )

            if file_path:
                target = Path(file_path)
                download_item.setDownloadDirectory(str(target.parent))
                download_item.setDownloadFileName(target.name)
                download_item.accept()
                print(f"Exported to: {file_path}")
            else:
                download_item.cancel()
                print("Export cancelled by user.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Jawless Notes")
    app.setOrganizationName("Jawless")
    app.setStyleSheet(GALLERY_STYLESHEET)

    if not WIKI_PATH.exists():
        print(f"Error: Could not find '{WIKI_PATH}'.")
        sys.exit(1)

    browser = TiddlyWikiBrowser(WIKI_PATH)
    browser.show()

    sys.exit(app.exec())
