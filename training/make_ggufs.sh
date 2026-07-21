#!/usr/bin/env bash
# Build BOTH quant variants (q8_0 + q4_K_M) of a merged model and import both into
# the dockerized Ollama. This is the STANDARD packaging step for every model version
# from v2 onward: ship a high-accuracy q8 (`pokered-8b-vN`) and a ~2x-faster q4
# (`pokered-8b-vN-q4`) for the throughput-bound fleet on the GB10 (bandwidth-limited,
# so fewer bytes/token = faster — see EVAL_q4.md).
#
# Usage: make_ggufs.sh <merged_dir> <out_dir> <ollama_name>
#   e.g. make_ggufs.sh training/runs/pokered-8b-v3/merged training/runs/pokered-8b-v3 pokered-8b-v3
set -euo pipefail

MERGED="${1:?merged model dir}"
OUTDIR="${2:?output dir}"
NAME="${3:?ollama base name, e.g. pokered-8b-v3}"
LLAMA="${LLAMA_CPP:-/tmp/llama.cpp}"
CONVERT="$LLAMA/convert_hf_to_gguf.py"
QUANT="$LLAMA/build-cpu/bin/llama-quantize"; [ -x "$QUANT" ] || QUANT="$(find "$LLAMA" -type f \( -name 'llama-quantize' -o -name 'quantize' \) 2>/dev/null | head -1)"

f16="$OUTDIR/.$NAME.f16.gguf"
q8="$OUTDIR/$NAME.q8_0.gguf"
q4="$OUTDIR/$NAME.q4_K_M.gguf"

echo ">> disk before:"; df -h "$OUTDIR" | tail -1
echo ">> [1/4] convert merged -> f16 gguf"
python3 "$CONVERT" "$MERGED" --outfile "$f16" --outtype f16

echo ">> [2/4] quantize -> q8_0"
"$QUANT" "$f16" "$q8" Q8_0
echo ">> [3/4] quantize -> q4_K_M"
"$QUANT" "$f16" "$q4" Q4_K_M
rm -f "$f16"   # temp f16 no longer needed

echo ">> [4/4] import BOTH into dockerized ollama"
for pair in "$q8:$NAME" "$q4:${NAME}-q4"; do
  gguf="${pair%%:*}"; mdl="${pair##*:}"
  printf 'FROM /models/%s\nPARAMETER temperature 0.1\nPARAMETER num_ctx 4096\n' "$(basename "$gguf")" > "$OUTDIR/Modelfile.$mdl"
  docker exec ollama mkdir -p /models
  docker cp "$gguf" "ollama:/models/$(basename "$gguf")"
  docker cp "$OUTDIR/Modelfile.$mdl" "ollama:/models/Modelfile.$mdl"
  docker exec ollama ollama create "$mdl" -f "/models/Modelfile.$mdl"
  echo "   imported $mdl  ($(du -h "$gguf" | cut -f1))"
done

echo ">> done. models: $NAME (q8, accuracy) and ${NAME}-q4 (q4, ~2x throughput)"
docker exec ollama ollama list | grep "$NAME" || true
