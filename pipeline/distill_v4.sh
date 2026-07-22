#!/usr/bin/env bash
# v4 distill chain: train LoRA on data/v4 (v2-navigation + starter/dialog skills) ->
# merge -> q8_0 + q4_K_M ggufs -> import into dockerized Ollama as pokered-8b-v4/-q4.
# Runs unattended; every stage logged with a marker so progress is greppable.
set -euo pipefail
cd "$(dirname "$0")/.."
export GBSKILL_GAME=pokemon_red
PY=training/.venv/bin/python
OUT=training/runs/pokered-8b-v4
mkdir -p "$OUT"
log(){ echo "[$(date +%H:%M:%S)] $*"; }

log "STAGE 1/4: train LoRA on data/v4"
$PY pipeline/train_lora.py --data data/v4 --model Qwen/Qwen3-8B --out runs/pokered-8b-v4
log "STAGE 1 done: adapter at $OUT/adapter"

log "STAGE 2/4: merge adapter into base"
$PY - <<'PY'
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
base, out = "Qwen/Qwen3-8B", "training/runs/pokered-8b-v4"
m = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16, device_map="cpu")
m = PeftModel.from_pretrained(m, out + "/adapter")
m = m.merge_and_unload()
m.save_pretrained(out + "/merged", safe_serialization=True)
AutoTokenizer.from_pretrained(out + "/adapter").save_pretrained(out + "/merged")
print("merged ->", out + "/merged")
PY
log "STAGE 2 done: merged at $OUT/merged"

log "STAGE 3/4: build q8_0 + q4_K_M ggufs and import to Ollama"
bash pipeline/make_ggufs.sh "$OUT/merged" "$OUT" pokered-8b-v4
log "STAGE 3 done: pokered-8b-v4 + pokered-8b-v4-q4 imported"

log "STAGE 4/4: sanity check both models exist"
docker exec ollama ollama list | grep -E "pokered-8b-v4" || { log "ERROR: v4 not imported"; exit 1; }
log "ALL DONE: v4 ready. Next: eval navigation-regression + starter + dialog."
