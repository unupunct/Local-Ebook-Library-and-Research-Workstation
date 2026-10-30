import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atlas.db import Database  # noqa: E402
from atlas.importer import Importer  # noqa: E402
from atlas.library import Library  # noqa: E402
from atlas.search import SearchEngine  # noqa: E402
from atlas.settings import Settings  # noqa: E402
from atlas.testdata import build_collection  # noqa: E402


class Env:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.settings = Settings(data_dir=str(tmp / "appdata"), index_threads=1)
        self.settings.ensure_dirs()
        self.db = Database(self.settings.path_db_dir() / "atlas.db", self.settings.path_index_dir() / "index.db")
        self.lib = Library(self.db, self.settings.path_cache_dir() / "covers")
        self.engine = SearchEngine(self.db)
        self.books = tmp / "books"

    def importer(self, **kw) -> Importer:
        return Importer(self.lib, self.settings, **kw)

    def book(self, rel: str):
        r = self.db.conn.execute("SELECT id FROM books WHERE rel_path=?", (rel.replace("/", "\\"),)).fetchone()
        if r is None:
            r = self.db.conn.execute("SELECT id FROM books WHERE rel_path=?", (rel,)).fetchone()
        return self.lib.get_book(r[0])


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


@pytest.fixture
def indexed(env):
    env.expect = build_collection(env.books)
    env.lib.add_root(env.books)
    env.importer().run()
    return env
