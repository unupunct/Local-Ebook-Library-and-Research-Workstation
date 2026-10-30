"""Performance benchmark on a synthetic collection. Prints MEASURED numbers; nothing is hard-coded.

    python scripts/benchmark.py 2000            # generate 2000 docs, index, query
    python scripts/benchmark.py 2000 --keep D:\bench   # keep generated files
"""
import argparse, ctypes, os, shutil, sys, tempfile, time
from ctypes import wintypes
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from atlas.db import Database
from atlas.importer import Importer
from atlas.library import Library
from atlas.search import SearchEngine
from atlas.settings import Settings
from atlas.testdata import build_bulk


def peak_rss_mb() -> float:
    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    c = PMC(); c.cb = ctypes.sizeof(c)
    k32, ps = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    ps.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PeakWorkingSetSize / 1048576


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("count", type=int, nargs="?", default=1000)
    ap.add_argument("--keep", default="")
    ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args()
    base = Path(a.keep) if a.keep else Path(tempfile.mkdtemp(prefix="atlas-bench-"))
    books, data = base / "books", base / "data"
    t = time.perf_counter()
    if not books.exists():
        build_bulk(books, a.count)
    gen = time.perf_counter() - t
    size_src = sum(f.stat().st_size for f in books.rglob("*") if f.is_file())
    s = Settings(data_dir=str(data), index_threads=a.threads); s.ensure_dirs()
    db = Database(s.path_db_dir() / "atlas.db", s.path_index_dir() / "index.db")
    lib = Library(db, s.path_cache_dir() / "covers")
    try:
        lib.add_root(books)
    except ValueError:
        pass  # re-using a kept benchmark folder
    t = time.perf_counter()
    p = Importer(lib, s).run()
    el = time.perf_counter() - t
    st = lib.stats()
    eng = SearchEngine(db)
    qs = ["soil", "soil AND water", '"battery cell"', "timber joint*", "wind NOT turbine", "(heat OR storage) AND panel"]
    lat = []
    for q in qs:
        eng.search(q, record_history=False)
        ts = []
        for _ in range(5):
            r = eng.search(q, record_history=False); ts.append(r.elapsed_ms)
        lat.append((q, min(ts), sorted(ts)[len(ts) // 2], r.total_passages))
    dbsize = sum(f.stat().st_size for f in s.path_db_dir().glob("*.db"))
    print(f"documents: {st['books']} ({size_src / 1048576:.1f} MB source, generated in {gen:.1f}s)")
    print(f"indexing: {el:.1f}s total = {st['books'] / el:.1f} docs/s, {size_src / 1048576 / el:.2f} MB/s  (threads={a.threads}, failed={p.failed})")
    print(f"segments: {st['segments']:,}   database+index size: {dbsize / 1048576:.1f} MB")
    print("search latency (best / median ms, passages matched):")
    for q, b, m, n in lat:
        print(f"  {q:32s} {b:8.1f} {m:8.1f}   {n:,}")
    print(f"peak working set: {peak_rss_mb():.0f} MB")
    db.close()
    if not a.keep:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
