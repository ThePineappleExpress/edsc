# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: builds a single-file `edsc` binary for the current OS.

Usage: pyinstaller edsc.spec

Set EDSC_ONEDIR=1 to build an unpacked `dist/edsc/` directory instead of a
single file - used by packaging/appimage/build-appimage.sh, where onefile's
extract-to-tmpdir-on-every-launch would be pointless inside an AppImage.
"""

import glob
import os
import sys

onedir = bool(os.environ.get("EDSC_ONEDIR"))

# Bundle every visual asset plus the Anti-Xeno briefing so the frozen build
# resolves them via paths.asset_path just like a checkout.
asset_datas = [
    (path, "edsc/assets")
    for pattern in ("*.png", "*.md")
    for path in glob.glob(os.path.join("edsc", "assets", pattern))
]

hiddenimports = []
binaries = []
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
elif sys.platform == "win32":
    # PySDL2 discovers the native runtime through pysdl2-dll. Preserve the
    # package-relative location expected by sdl2dll.get_dllpath().
    from PyInstaller.utils.hooks import get_package_paths

    _, sdl2dll_dir = get_package_paths("sdl2dll")
    binaries.append(
        (os.path.join(sdl2dll_dir, "dll", "SDL2.dll"), "sdl2dll/dll")
    )
    hiddenimports += ["sdl2", "sdl2dll"]

a = Analysis(
    ["edsc/__main__.py"],
    pathex=[],
    binaries=binaries,
    datas=asset_datas,
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
    *([] if onedir else [a.binaries, a.datas]),
    [],
    exclude_binaries=onedir,
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

if onedir:
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="edsc",
    )
