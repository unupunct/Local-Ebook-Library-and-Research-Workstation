"""First-run wizard: books folder, optional external drive, data location, initial indexing."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
                               QWizard, QWizardPage)

from .. import APP_NAME
from ..paths import default_data_dir
from ..settings import Settings


def _picker(edit: QLineEdit, parent) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    b = QPushButton("Browse…")
    b.clicked.connect(lambda: edit.setText(QFileDialog.getExistingDirectory(parent, "Choose folder", edit.text()) or edit.text()))
    row.addWidget(b)
    return row


class FirstRunWizard(QWizard):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Welcome to {APP_NAME}")
        self.setWizardStyle(QWizard.ModernStyle)
        self.settings = settings

        p1 = QWizardPage(); p1.setTitle("Welcome")
        l = QVBoxLayout(p1)
        t = QLabel(f"{APP_NAME} organises, searches and reads your personal ebook collection.\n\n"
                   "• Your books stay where they are; nothing is moved or modified.\n"
                   "• Everything works offline; no account or cloud service is needed.\n"
                   "• You can change every choice later in Settings.")
        t.setWordWrap(True); l.addWidget(t)
        self.addPage(p1)

        p2 = QWizardPage(); p2.setTitle("Where are your books?"); p2.setSubTitle("Choose the main folder. Subfolders are included. You may skip this and add sources later.")
        f2 = QFormLayout(p2)
        self.books = QLineEdit(); self.books.setPlaceholderText("e.g. D:\\Books")
        self.extra = QLineEdit(); self.extra.setPlaceholderText("optional: a USB drive or second folder")
        f2.addRow("Main books folder", _picker(self.books, self)); f2.addRow("Another folder or external drive", _picker(self.extra, self))
        n = QLabel("External drives may be disconnected later: your library data, notes and reading progress stay available, and the books reconnect when the drive returns.")
        n.setWordWrap(True); f2.addRow(n)
        self.addPage(p2)

        p3 = QWizardPage(); p3.setTitle("Where should Atlas keep its own data?")
        p3.setSubTitle("The database, search index, covers and logs. Keep this on an internal drive for speed.")
        f3 = QFormLayout(p3)
        self.data = QLineEdit(settings.data_dir or str(default_data_dir()))
        f3.addRow("Application data folder", _picker(self.data, self))
        n3 = QLabel("This is separate from your books. The default (your local Windows application-data folder) is a good choice.")
        n3.setWordWrap(True); f3.addRow(n3)
        self.addPage(p3)

        p4 = QWizardPage(); p4.setTitle("Initial indexing")
        l4 = QVBoxLayout(p4)
        self.now = QCheckBox("Scan and index my books right after setup"); self.now.setChecked(True)
        l4.addWidget(self.now)
        n4 = QLabel("Indexing reads every document once to make it searchable. For large collections this can take a while and runs in the background; "
                    "you can keep using the app, cancel, and resume later. Untick this to postpone it.")
        n4.setWordWrap(True); l4.addWidget(n4)
        self.addPage(p4)

    def result_config(self) -> dict:
        folders = [p.strip() for p in (self.books.text(), self.extra.text()) if p.strip()]
        data = self.data.text().strip()
        default = str(default_data_dir())
        return {"folders": folders, "data_dir": "" if Path(data) == Path(default) else data, "index_now": self.now.isChecked()}
