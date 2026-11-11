"""Build the Cortex Windows executable with PyInstaller.

Run it through BUILD_EXE.bat, or directly:

    pip install pyinstaller
    python build_exe.py

Output: dist/Cortex/Cortex.exe  (launch it with START_SCANNER.bat)

The bundle deliberately leaves out matplotlib, plotly and Qt WebEngine: the
new interface draws its charts natively, so none of them are needed. That
keeps the build a few hundred megabytes smaller and makes it start faster.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
SPEC = ROOT / "Cortex.spec"
APP_NAME = "Cortex"
ENTRY = "cortex_app.py"
SEP = ";" if os.name == "nt" else ":"
ICON_NAMES = ("cortex_icon.ico", "cortex_icon.ico.ico")

# Local modules PyInstaller might miss (scanner is imported lazily at runtime).
HIDDEN = ["scanner", "cortex_app", "cortex_chart", "cortex_theme", "cortex_indicators"]

# Not used by the new interface — excluding them shrinks the bundle a lot.
EXCLUDED = [
    "matplotlib", "plotly", "scipy", "tkinter", "customtkinter",
    "PyQt5", "PyQt6", "PySide2", "IPython", "jupyter", "notebook",
    "pytest", "sphinx", "dask", "unittest", "pydoc", "doctest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.Qt3DCore",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtLocation", "PySide6.QtPositioning",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSql",
    "PySide6.QtNetworkAuth", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
]

# Packages whose data files must travel with the bundle.
COLLECT = ["yfinance"]


def resolve_icon() -> Path | None:
    for name in ICON_NAMES:
        path = ROOT / name
        if path.exists():
            return path
    return None


def main() -> int:
    try:
        import PyInstaller.__main__
    except ImportError:
        raise SystemExit("PyInstaller is not installed.\n"
                         "Install it first:  pip install pyinstaller")

    if not (ROOT / ENTRY).exists():
        raise SystemExit(f"Cannot find {ENTRY} next to this script.")

    for path in (BUILD, DIST):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    if SPEC.exists():
        SPEC.unlink()

    args = [
        str(ROOT / ENTRY),
        "--name", APP_NAME,
        "--windowed",          # no console window
        "--onedir",            # folder build: starts much faster than --onefile
        "--clean",
        "--noconfirm",
        "--noupx",             # UPX can corrupt Qt DLLs and trips antivirus
    ]

    icon = resolve_icon()
    if icon is not None:
        args += ["--icon", str(icon), "--add-data", f"{icon}{SEP}."]
        print(f"Using icon: {icon.name}")
    else:
        print("WARNING: no cortex_icon.ico found — building without a custom icon.")

    for module in HIDDEN:
        args += ["--hidden-import", module]
    for package in COLLECT:
        args += ["--collect-all", package]
    for module in EXCLUDED:
        args += ["--exclude-module", module]

    print(f"Building {APP_NAME} from {ENTRY} ...")
    PyInstaller.__main__.run(args)

    exe = DIST / APP_NAME / f"{APP_NAME}.exe"
    if exe.exists():
        size = sum(f.stat().st_size for f in (DIST / APP_NAME).rglob("*") if f.is_file())
        print(f"\nBuilt: {exe}\nBundle size: {size / 1_048_576:.0f} MB")
    else:
        print("\nBuild finished but the executable was not found. Check the log above.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
