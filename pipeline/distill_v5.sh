#!/usr/bin/env bash
# v5 distill chain, Q4 ONLY (per request -- skip the q8 quant to save time).
# train LoRA on data/v5 (v4 + errand demos) -> merge -> q4_K_M gguf -> import as
# pokered-8b-v5-q4. Runs unattended; every stage logged with a marker.
set -euo pipefail
cd "$(dirname "$0")/.."
export GBSKILL_GAME=pokemon_red
export PATH="$(pwd)/training/.venv/bin:$PATH"   # so make_ggufs' python3 has torch
PY=training/.venv/bin/python
OUT=training/runs/pokered-8b-v5
LLAMA="${LLAMA_CPP:-/tmp/llama.cpp}"
QUANT="$LLAMA/build-cpu/bin/llama-quantize"; [ -x "$QUANT" ] || QUANT="$(find "$LLAMA" -type f -name 'llama-quantize' 2>/dev/null | head -1)"
mkdir -p "$OUT"
log(){ echo "[$(date +%H:%M:%S)] $*"; }

log "STAGE 1/4: train LoRA on data/v5"
$PY pipeline/train_lora.py --data data/v5 --model Qwen/Qwen3-8B --out runs/pokered-8b-v5
log "STAGE 1 done: adapter at $OUT/adapter"

log "STAGE 2/4: merge adapter into base"
$PY - <<'PY'
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
base, out = "Qwen/Qwen3-8B", "training/runs/pokered-8b-v5"
m = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16, device_map="cpu")
m = PeftModel.from_pretrained(m, out + "/adapter"); m = m.merge_and_unload()
m.save_pretrained(out + "/merged", safe_serialization=True)
AutoTokenizer.from_pretrained(out + "/adapter").save_pretrained(out + "/merged")
print("merged ->", out + "/merged")
PY
log "STAGE 2 done: merged at $OUT/merged"

log "STAGE 3/4: convert -> f16 -> quantize q4_K_M ONLY"
f16="$OUT/.pokered-8b-v5.f16.gguf"; q4="$OUT/pokered-8b-v5.q4_K_M.gguf"
python3 "$LLAMA/convert_hf_to_gguf.py" "$OUT/merged" --outfile "$f16" --outtype f16
"$QUANT" "$f16" "$q4" Q4_K_M
rm -f "$f16"
log "STAGE 3 done: $q4"

log "STAGE 4/4: import into dockerized Ollama as pokered-8b-v5-q4"
docker exec ollama mkdir -p /models
docker cp "$q4" "ollama:/models/$(basename "$q4")"
printf 'FROM /models/%s\nPARAMETER temperature 0.1\nPARAMETER num_ctx 4096\n' "$(basename "$q4")" > "$OUT/Modelfile.v5-q4"
docker cp "$OUT/Modelfile.v5-q4" ollama:/models/Modelfile.v5-q4
docker exec ollama ollama create pokered-8b-v5-q4 -f /models/Modelfile.v5-q4
docker exec ollama ollama list | grep -E "pokered-8b-v5" || { log "ERROR: v5 not imported"; exit 1; }
log "ALL DONE: pokered-8b-v5-q4 ready. Next: eval navigation + errand + starter/dialog."
