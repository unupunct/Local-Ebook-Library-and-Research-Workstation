# PyInstaller spec: one-folder Windows build.  Run via build.ps1
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
EXCLUDES = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore", "PySide6.QtQuick",
            "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
            "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNetworkAuth", "PySide6.QtSensors", "PySide6.QtSerialPort",
            "PySide6.QtWebSockets", "PySide6.QtWebChannel", "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtRemoteObjects",
            "tkinter", "pytest", "pytest_qt", "PyInstaller"]

a = Analysis(["run_atlas.py"], pathex=["."], binaries=[], datas=[("assets/atlas.ico", "assets"), ("assets/atlas.png", "assets")],
             hiddenimports=collect_submodules("atlas") + ["lxml.etree", "lxml._elementpath", "pymupdf"],
             hookspath=[], runtime_hooks=[], excludes=EXCLUDES, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AtlasLibrary", debug=False, strip=False, upx=False, console=False,
          icon="assets/atlas.ico", version="build_tools/version_info.txt")
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AtlasLibrary")
