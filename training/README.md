# Custom Pokémon Red player model — training pipeline

Design: `docs/superpowers/specs/2026-07-20-custom-model-design.md`
Approach: RAG (facts, deterministic keyed retrieval) + LoRA (behavior), Qwen3-8B → Qwen3-30B-A3B.

## Layout

| File | Purpose |
|---|---|
| `context.py` | Shared RAG context builder over `assets/json` (battle matchups, encounter tables, learnsets). Used by dataset build AND serving — train/inference parity. |
| `prompts.py` | System prompt + example formatter (Qwen3 chat + `<think>` traces). |
| `synth/battle.py` | Ground-truth battle decisions: best-move (damage proxy = power × type × STAB × acc), catch-only-Pikachu policy, fight/flee. |
| `synth/nav.py` | Warp/door targets and map connections from `assets/gamedata/maps/{objects,headers}`. |
| `synth/meta.py` | Heal timing, switch-to-protect (the Pikachu XP trick), 0-PP recovery, menu/dialog-trap recovery. |
| `mine_journals.py` | Real play journals → deduped decision examples (outcome=Done only). |
| `build_dataset.py` | Assemble → shuffle → split → `data/vN/{train,val,test}.jsonl` + stats. |
| `train_lora.py` | bf16 LoRA via HF PEFT (no bitsandbytes — GB10-safe). `--smoke` validates the stack. |
| `eval_offline.py` | Valid-JSON / action-match / battle-move-optimality vs held-out test set, via Ollama. |

## Runbook

```bash
# 0) env (done once) — venv at training/.venv, torch cu130 aarch64
training/.venv/bin/python -c "import torch; print(torch.cuda.is_available())"

# 1) build dataset
python3 training/build_dataset.py v1        # -> training/data/v1/

# 2) smoke-test the training stack (Qwen3-0.6B, 8 steps)
cd training && .venv/bin/python train_lora.py --smoke

# 3) real run (8B)
.venv/bin/python train_lora.py --data data/v1 --model Qwen/Qwen3-8B --out runs/pokered-8b-v1

# 4) merge + serve via Ollama
.venv/bin/python -c "
from peft import PeftModel; from transformers import AutoModelForCausalLM, AutoTokenizer
import torch
m=AutoModelForCausalLM.from_pretrained('Qwen/Qwen3-8B',dtype=torch.bfloat16)
m=PeftModel.from_pretrained(m,'runs/pokered-8b-v1/adapter').merge_and_unload()
m.save_pretrained('runs/pokered-8b-v1/merged'); AutoTokenizer.from_pretrained('Qwen/Qwen3-8B').save_pretrained('runs/pokered-8b-v1/merged')"
# convert to GGUF with llama.cpp's convert_hf_to_gguf.py, then:
#   ollama create pokered-8b -f Modelfile   (FROM ./pokered-8b.gguf)

# 5) evaluate (stock baseline vs tuned)
python3 training/eval_offline.py --data data/v1 --ollama qwen3:8b
python3 training/eval_offline.py --data data/v1 --ollama pokered-8b
```

## Dataset v1 (7,099 examples, ~2.5M train tokens)

battle_move 4000 · nav_warp 1338 · flee 256 · fight_wild 244 · switch_protect 250 ·
journal 199 · heal 250 · menu_recovery 150 · pp_recovery 150 · nav_connection 162 · catch 100

Every example: `[FACTS] [STATE] [GOAL] → <think>…</think> {action JSON}`.

## Next iterations
- v2: Claude-teacher rollouts via gb-agent (rejection-sampled on RAM progress signals),
  DAgger-style corrections of student mistakes, live scenario eval from `run/ck-*.state`.
- 30B: same pipeline, `--model Qwen/Qwen3-30B-A3B` (fits in 128GB unified in bf16).
