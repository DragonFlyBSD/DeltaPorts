#!/bin/sh
# Convert the JXL GNOME wallpapers to raster JPG and repoint the
# gnome-background-properties metadata. SVG wallpapers are kept as SVG and
# are loaded by glycin's external SVG loader.
#   $1  backgrounds dir   (.../share/backgrounds/gnome)
#   $2  properties dir    (.../share/gnome-background-properties)
#   $3  path to dfly-resize.py
set -e
BG="$1"
PROPS="$2"
RESIZE="$3"

cd "$BG"

for f in *.jxl; do
    [ -e "$f" ] || continue
    base=${f%.jxl}
    djxl "$f" "$base.tmp.png" >/dev/null 2>&1
    python3 "$RESIZE" "$base.tmp.png" "$base.jpg" 2560 1440
    rm -f "$f" "$base.tmp.png"
done


# repoint the metadata: <filename>...jxl</filename> -> .jpg
for x in "$PROPS"/*.xml; do
    [ -e "$x" ] || continue
    sed -i '' -e 's/\.jxl</.jpg</g' "$x"
done
