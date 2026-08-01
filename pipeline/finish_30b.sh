#!/usr/bin/env bash
# Resume the 30B distill after the peft/transformers merge bug: MANUAL LoRA merge (add
# B@A*scaling to the base weights directly -- bypasses peft's WeightConverter path that
# crashes on the MoE), then gguf q4 + import. The trained adapter is already on disk.
set -euo pipefail
cd "$(dirname "$0")/.."
export GBSKILL_GAME=pokemon_red
export PATH="$(pwd)/training/.venv/bin:$PATH"
PY=training/.venv/bin/python
OUT=training/runs/pokered-30b-a3b-v1
LLAMA="${LLAMA_CPP:-/tmp/llama.cpp}"
QUANT="$LLAMA/build-cpu/bin/llama-quantize"; [ -x "$QUANT" ] || QUANT="$(find "$LLAMA" -type f -name 'llama-quantize' 2>/dev/null | head -1)"
log(){ echo "[$(date +%H:%M:%S)] $*"; }

log "STAGE 2 (manual merge -- bypasses peft weight-conversion bug)"
$PY - <<'PY'
import torch, json, collections
from safetensors.torch import load_file
from transformers import AutoModelForCausalLM, AutoTokenizer
base, out = "Qwen/Qwen3-30B-A3B", "training/runs/pokered-30b-a3b-v1"
cfg = json.load(open(out + "/adapter/adapter_config.json"))
scaling = cfg["lora_alpha"] / cfg["r"]
print("loading base (60GB, cpu)...", flush=True)
m = AutoModelForCausalLM.from_pretrained(base, dtype=torch.bfloat16, device_map="cpu")
adapter = load_file(out + "/adapter/adapter_model.safetensors")
mods = collections.defaultdict(dict)
for k, v in adapter.items():
    p = k.replace("base_model.model.", "").rsplit(".lora_", 1)[0] + ".weight"
    mods[p]["A" if ".lora_A." in k else "B"] = v
sd = dict(m.named_parameters())
merged = 0
for p, ab in mods.items():
    if "A" in ab and "B" in ab and p in sd:
        delta = (ab["B"].to(torch.float32) @ ab["A"].to(torch.float32)) * scaling
        with torch.no_grad():
            sd[p].add_(delta.to(sd[p].dtype))
        merged += 1
print(f"merged {merged} modules (expected {len(mods)})", flush=True)
assert merged == len(mods), "some LoRA modules did not map to base params"
m.save_pretrained(out + "/merged", safe_serialization=True)
AutoTokenizer.from_pretrained(out + "/adapter").save_pretrained(out + "/merged")
print("saved merged ->", out + "/merged", flush=True)
PY
log "STAGE 2 done"

log "STAGE 3: convert -> f16 gguf -> quantize q4_K_M"
f16="$OUT/.m.f16.gguf"; q4="$OUT/pokered-30b-a3b-v1.q4_K_M.gguf"
python3 "$LLAMA/convert_hf_to_gguf.py" "$OUT/merged" --outfile "$f16" --outtype f16
"$QUANT" "$f16" "$q4" Q4_K_M
rm -f "$f16"
log "STAGE 3 done: $q4"

log "STAGE 4: import into Ollama as pokered-30b-a3b-v1"
docker exec ollama mkdir -p /models
docker cp "$q4" "ollama:/models/$(basename "$q4")"
printf 'FROM /models/%s\nPARAMETER temperature 0.1\nPARAMETER num_ctx 4096\n' "$(basename "$q4")" > "$OUT/Modelfile.30b"
docker cp "$OUT/Modelfile.30b" ollama:/models/Modelfile.30b
docker exec ollama ollama create pokered-30b-a3b-v1 -f /models/Modelfile.30b
docker exec ollama ollama list | grep -E "pokered-30b" || { log "ERROR: not imported"; exit 1; }
log "ALL DONE: pokered-30b-a3b-v1 ready."
