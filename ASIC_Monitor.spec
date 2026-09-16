# Build only through scripts/build_windows.py: it validates release metadata first.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
resources = collect_data_files("miner_scanner.profiles")
resources += [(str(root / name), ".") for name in ("version.json", "app.ico", "LICENSE")]
a = Analysis(
    [str(root / "gemini_gui.py")], pathex=[str(root)], binaries=[], datas=resources,
    hiddenimports=["openpyxl", "PyQt6.QtSvg"],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "IPython", "pytest"], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name="ASIC_Monitor",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    icon=str(root / "app.ico"), version=str(root / "build-windows" / "version_info.txt"),
)
