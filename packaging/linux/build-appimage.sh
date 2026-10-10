#!/usr/bin/env bash
# The Linux app as one AppImage file, from the PyInstaller folder:
#   packaging/linux/build-appimage.sh dist/mems-sketch VERSION OUT_DIR
# Downloads appimagetool (a fixed release) when it is not on PATH.
set -euo pipefail
bundle=$1 version=$2 out=$3
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
appdir=$work/MEMS_Sketch.AppDir
mkdir -p "$appdir/usr/lib" "$out"
cp -a "$bundle" "$appdir/usr/lib/mems-sketch"
cp "$here/../icons/mems-sketch.png" "$appdir/mems-sketch.png"
cp "$here/mems-sketch.desktop" "$appdir/mems-sketch.desktop"
cat > "$appdir/AppRun" <<'RUN'
#!/bin/sh
here=$(dirname "$(readlink -f "$0")")
exec "$here/usr/lib/mems-sketch/mems-sketch" "$@"
RUN
chmod +x "$appdir/AppRun"

command -v desktop-file-validate >/dev/null || {
    echo "desktop-file-validate is needed (Debian/Ubuntu: apt install desktop-file-utils)" >&2
    exit 1
}
tool=$(command -v appimagetool || true)
if [ -z "$tool" ]; then
    tool=$work/appimagetool
    curl -fsSL -o "$tool" \
        https://github.com/AppImage/appimagetool/releases/download/1.9.0/appimagetool-x86_64.AppImage
    chmod +x "$tool"
fi
# Without FUSE (containers, CI) the tool runs from its extracted contents.
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$tool" --no-appstream "$appdir" \
    "$out/MEMS_Sketch-$version-x86_64.AppImage"
rm -rf "$work"
