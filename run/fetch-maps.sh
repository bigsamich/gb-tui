#!/bin/bash
# Download map block data + blocksets from the pokered disassembly into run/maps/.
# These are extracted game data and are never committed (see .gitignore).
set -e
BASE=https://raw.githubusercontent.com/pret/pokered/master
DEST="$(dirname "$0")/maps"
mkdir -p "$DEST"
for f in PalletTown ViridianCity PewterCity CeruleanCity Route1 Route2 Route3 Route4 ViridianForest MtMoon1F MtMoonB1F MtMoonB2F; do
  curl -sfL -o "$DEST/$f.blk" "$BASE/maps/$f.blk"
done
for b in overworld forest cavern; do
  curl -sfL -o "$DEST/$b.bst" "$BASE/gfx/blocksets/$b.bst"
done
ls -la "$DEST"
