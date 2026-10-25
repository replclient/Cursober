#!/bin/sh
set -eu

# Requires PyInstaller, rsvg-convert, and an official appimagetool AppImage.
PROJECT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD=${BUILD:-"$PROJECT/build"}
APPIMAGETOOL=${APPIMAGETOOL:?Set APPIMAGETOOL to the official appimagetool AppImage path.}

# The simplified titlebar icon stays crisp at 16–128px; the full mark is used
# for the large desktop icon sizes.
for SIZE in 16 32 48 64 128; do
  rsvg-convert --width "$SIZE" --height "$SIZE" "$PROJECT/assets/cursober-titlebar.svg" > "$PROJECT/assets/cursober-$SIZE.png"
done
for SIZE in 256 512; do
  rsvg-convert --width "$SIZE" --height "$SIZE" "$PROJECT/assets/cursober.svg" > "$PROJECT/assets/cursober-$SIZE.png"
done

python3 -m PyInstaller --noconfirm --clean --windowed --onedir --name Cursober \
  --add-data "$PROJECT/assets:assets" \
  --distpath "$BUILD/dist" --workpath "$BUILD/pyinstaller-work" --specpath "$BUILD" \
  "$PROJECT/src/cursober.py"

APPDIR="$BUILD/Cursober.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/scalable/apps"
cp -a "$BUILD/dist/Cursober" "$APPDIR/usr/bin/Cursober"
install -m 0644 "$PROJECT/packaging/cursober.desktop" "$APPDIR/usr/share/applications/cursober.desktop"
install -m 0644 "$PROJECT/assets/cursober.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/cursober.svg"
for SIZE in 16 32 48 64 128 256 512; do
  ICONDIR="$APPDIR/usr/share/icons/hicolor/${SIZE}x${SIZE}/apps"
  mkdir -p "$ICONDIR"
  install -m 0644 "$PROJECT/assets/cursober-$SIZE.png" "$ICONDIR/cursober.png"
done
install -m 0755 "$PROJECT/packaging/AppRun" "$APPDIR/AppRun"
ln -s usr/share/applications/cursober.desktop "$APPDIR/cursober.desktop"
ln -s usr/share/icons/hicolor/256x256/apps/cursober.png "$APPDIR/cursober.png"
"$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "$PROJECT/Cursober-x86_64.AppImage"
