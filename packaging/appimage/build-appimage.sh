#!/usr/bin/env bash
# Build dist/edsc-x86_64.AppImage - the SteamOS / Steam Deck friendly build
# (single file, no install, works on an immutable root filesystem).
#
# Requires: a Python environment with edsc and pyinstaller installed, curl.
# Usage:    bash packaging/appimage/build-appimage.sh
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo_root"

# 1. Unpacked PyInstaller build (EDSC_ONEDIR is handled by edsc.spec).
EDSC_ONEDIR=1 pyinstaller --noconfirm edsc.spec

# 2. Assemble the AppDir.
appdir=dist/AppDir
rm -rf "$appdir"
mkdir -p "$appdir/usr/bin"
cp -a dist/edsc/. "$appdir/usr/bin/"
cp packaging/aur/edsc.desktop "$appdir/edsc.desktop"
cp edsc/assets/icon.png "$appdir/edsc.png"
ln -sf edsc.png "$appdir/.DirIcon"
cat > "$appdir/AppRun" <<'EOF'
#!/bin/sh
exec "$(dirname "$0")/usr/bin/edsc" "$@"
EOF
chmod +x "$appdir/AppRun"

# 3. Pack it with appimagetool. --appimage-extract-and-run avoids needing
#    FUSE on build machines (CI runners, containers).
tool=dist/appimagetool
if [ ! -x "$tool" ]; then
  curl -sfL --retry 5 --retry-delay 3 --retry-all-errors -o "$tool" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
  chmod +x "$tool"
fi
ARCH=x86_64 "$tool" --appimage-extract-and-run "$appdir" dist/edsc-x86_64.AppImage

echo "Built dist/edsc-x86_64.AppImage"
