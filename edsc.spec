# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: builds a single-file `edsc` binary for the current OS.

Usage: pyinstaller edsc.spec
"""

import sys

hiddenimports = []
if sys.platform.startswith("linux"):
    # The X11 helpers import Xlib lazily inside functions; list the pieces
    # explicitly so PyInstaller's analysis can't miss any of them.
    hiddenimports += [
        "Xlib",
        "Xlib.display",
        "Xlib.error",
        "Xlib.protocol",
        "Xlib.X",
        "Xlib.XK",
        "Xlib.ext",
        "Xlib.ext.shape",
    ]

a = Analysis(
    ["edsc/__main__.py"],
    pathex=[],
    binaries=[],
    datas=[("edsc/assets/icon.png", "edsc/assets")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Executable icon: Windows wants an .ico, other platforms take the PNG.
if sys.platform.startswith("win"):
    _exe_icon = "icon.ico"
else:
    _exe_icon = "icon.png"

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="edsc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # No console window on Windows; overlay logging goes nowhere anyway.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_exe_icon,
)
