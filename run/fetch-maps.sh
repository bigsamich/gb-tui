#!/bin/bash
# Download map block data + blocksets from the pokered disassembly into run/maps/.
# These are extracted game data and are never committed (see .gitignore).
set -e
BASE=https://raw.githubusercontent.com/pret/pokered/master
DEST="$(dirname "$0")/maps"
mkdir -p "$DEST"
for f in PalletTown ViridianCity PewterCity Route1 Route2 ViridianForest; do
  curl -sfL -o "$DEST/$f.blk" "$BASE/maps/$f.blk"
done
for b in overworld forest; do
  curl -sfL -o "$DEST/$b.bst" "$BASE/gfx/blocksets/$b.bst"
done
ls -la "$DEST"
