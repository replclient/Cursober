#!/bin/sh
set -eu

# Requires PyInstaller, rsvg-convert, and an official appimagetool AppImage.
PROJECT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD=${BUILD:-"$PROJECT/build"}
APPIMAGETOOL=${APPIMAGETOOL:?Set APPIMAGETOOL to the official appimagetool AppImage path.}

# The same full SVG is rendered for every icon size, including the titlebar.
for SIZE in 16 32 48 64 128 256 512; do
  rsvg-convert --width "$SIZE" --height "$SIZE" "$PROJECT/assets/veyro.svg" > "$PROJECT/assets/veyro-$SIZE.png"
done

python3 -m PyInstaller --noconfirm --clean --windowed --onedir --name veyro \
  --add-data "$PROJECT/assets:assets" \
  --distpath "$BUILD/dist" --workpath "$BUILD/pyinstaller-work" --specpath "$BUILD" \
  "$PROJECT/src/veyro.py"

APPDIR="$BUILD/veyro.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/scalable/apps"
cp -a "$BUILD/dist/veyro" "$APPDIR/usr/bin/veyro"
install -m 0644 "$PROJECT/packaging/veyro.desktop" "$APPDIR/usr/share/applications/veyro.desktop"
install -m 0644 "$PROJECT/assets/veyro.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/veyro.svg"
for SIZE in 16 32 48 64 128 256 512; do
  ICONDIR="$APPDIR/usr/share/icons/hicolor/${SIZE}x${SIZE}/apps"
  mkdir -p "$ICONDIR"
  install -m 0644 "$PROJECT/assets/veyro-$SIZE.png" "$ICONDIR/veyro.png"
done
install -m 0755 "$PROJECT/packaging/AppRun" "$APPDIR/AppRun"
ln -s usr/share/applications/veyro.desktop "$APPDIR/veyro.desktop"
ln -s usr/share/icons/hicolor/256x256/apps/veyro.png "$APPDIR/veyro.png"
"$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "$PROJECT/veyro-x86_64.AppImage"
