"""Crash bisection: python ui_probe2.py <mode>. Prints progress; exit code shows whether the process died at exit."""
import faulthandler
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

faulthandler.enable(file=sys.stdout, all_threads=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
mode = sys.argv[1]

from PySide6.QtCore import QCoreApplication, QThread
from PySide6.QtWidgets import QApplication

from atlas.db import Database
from atlas.importer import Importer
from atlas.library import Library
from atlas.settings import Settings
from atlas.testdata import build_collection

tmp = Path(tempfile.mkdtemp())
books = tmp / "books"
build_collection(books)
s = Settings(data_dir=str(tmp / "data"), index_threads=2)
s.ensure_dirs()
db = Database(s.path_db_dir() / "atlas.db", s.path_index_dir() / "index.db")
lib = Library(db, s.path_cache_dir() / "covers")
lib.add_root(books)
app = QApplication([]) if mode != "coreapp-qthread" else QCoreApplication([])


def work():
    Importer(lib, s, lambda p: None).run()


if mode == "plain-thread":
    t = threading.Thread(target=work); t.start(); t.join()
elif mode in ("qthread", "coreapp-qthread"):
    class T(QThread):
        def run(self):
            work()
    t = T(); t.start(); t.wait()
elif mode == "main-thread":
    work()
elif mode == "no-import":
    pass
elif mode == "single-index-thread":
    s.index_threads = 1
    t = threading.Thread(target=work); t.start(); t.join()
db.close()
print("body done", mode, flush=True)
