#!/usr/bin/env bash
# Fine-tune Qwen3-30B-A3B (MoE, 128 experts / 8 active) on the v7 dataset -> merge ->
# q4_K_M -> import as pokered-30b-a3b-v1. Attention-only LoRA (targeting the expert FFNs
# would explode across 128 experts). batch=1/accum=64 to fit the 60GB model in memory.
# First run downloads ~60GB of base weights. Q4 only.
set -euo pipefail
cd "$(dirname "$0")/.."
export GBSKILL_GAME=pokemon_red
export PATH="$(pwd)/training/.venv/bin:$PATH"
PY=training/.venv/bin/python
BASE="Qwen/Qwen3-30B-A3B"
OUT=training/runs/pokered-30b-a3b-v1
LLAMA="${LLAMA_CPP:-/tmp/llama.cpp}"
QUANT="$LLAMA/build-cpu/bin/llama-quantize"; [ -x "$QUANT" ] || QUANT="$(find "$LLAMA" -type f -name 'llama-quantize' 2>/dev/null | head -1)"
mkdir -p "$OUT"
log(){ echo "[$(date +%H:%M:%S)] $*"; }

log "STAGE 1/4: LoRA fine-tune on data/v7 (Qwen3-30B-A3B, attention-only). First run downloads ~60GB."
$PY pipeline/train_lora.py --data data/v7 --model "$BASE" --out runs/pokered-30b-a3b-v1 \
    --target-modules "q_proj,k_proj,v_proj,o_proj" --batch 1 --accum 64
log "STAGE 1 done: adapter at $OUT/adapter"

log "STAGE 2/4: merge adapter into base (CPU, ~60GB)"
$PY - <<'PY'
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
base, out = "Qwen/Qwen3-30B-A3B", "training/runs/pokered-30b-a3b-v1"
m = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16, device_map="cpu")
m = PeftModel.from_pretrained(m, out + "/adapter"); m = m.merge_and_unload()
m.save_pretrained(out + "/merged", safe_serialization=True)
AutoTokenizer.from_pretrained(out + "/adapter").save_pretrained(out + "/merged")
print("merged ->", out + "/merged")
PY
log "STAGE 2 done: merged"

log "STAGE 3/4: convert -> f16 gguf -> quantize q4_K_M"
f16="$OUT/.m.f16.gguf"; q4="$OUT/pokered-30b-a3b-v1.q4_K_M.gguf"
python3 "$LLAMA/convert_hf_to_gguf.py" "$OUT/merged" --outfile "$f16" --outtype f16
"$QUANT" "$f16" "$q4" Q4_K_M
rm -f "$f16"
log "STAGE 3 done: $q4"

log "STAGE 4/4: import into Ollama as pokered-30b-a3b-v1"
docker exec ollama mkdir -p /models
docker cp "$q4" "ollama:/models/$(basename "$q4")"
printf 'FROM /models/%s\nPARAMETER temperature 0.1\nPARAMETER num_ctx 4096\n' "$(basename "$q4")" > "$OUT/Modelfile.30b"
docker cp "$OUT/Modelfile.30b" ollama:/models/Modelfile.30b
docker exec ollama ollama create pokered-30b-a3b-v1 -f /models/Modelfile.30b
docker exec ollama ollama list | grep -E "pokered-30b" || { log "ERROR: not imported"; exit 1; }
log "ALL DONE: pokered-30b-a3b-v1 ready. Next: A/B vs v7-8B on hard exploration decisions."
