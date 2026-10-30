# Release checklist

- [ ] Bump `__version__` in `atlas/__init__.py` and `build_tools/version_info.txt`
- [ ] `.\.venv\Scripts\python -m pytest -q` is green (Tesseract test skips if not installed)
- [ ] `.\build.ps1` succeeds (it re-runs the tests)
- [ ] `dist\AtlasLibrary\AtlasLibrary.exe --selftest out.txt` prints `SELFTEST OK`
- [ ] Start the exe on a clean user profile: wizard appears, DB created under `%LOCALAPPDATA%\AtlasLibrary`, nothing written next to the exe
- [ ] Import a real folder, search, open a PDF and an EPUB result at the right page/chapter
- [ ] Unplug/rename the source folder → books show OFFLINE, notes still open; restore → reconnects
- [ ] Back up and restore on a throw-away data folder
- [ ] Update `STATUS.md` (test results, benchmark numbers, known limitations)
- [ ] (Optional) sign `AtlasLibrary.exe`; build installer with Inno Setup
