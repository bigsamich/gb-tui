# Custom Pokémon Red player model — design

Goal: a small local model (8B → 30B) that plays Pokémon Red through the existing
gb-agent harness, built by combining RAG (facts) with LoRA fine-tuning (behavior),
trained locally on the NVIDIA GB10 (~128GB unified memory).

Decisions locked with the user:
- **Approach A**: two-layer — RAG supplies facts, LoRA supplies skill. Sequence B→A:
  stand up the RAG baseline on a stock model first, then distill into a LoRA.
- **Interface**: text game-state → high-level action JSON (`walk_to`, `fight`, …),
  the vocabulary the harness already executes. No vision, no raw buttons.
- **Base model**: Qwen3-8B (dense) for pipeline iteration → Qwen3-30B-A3B (MoE) for
  production. Apache-2.0. Reasoning traces use Qwen3's native `<think>` format.
- **Reasoning**: short traces (1–3 sentences), not verbose CoT. Thinking can be
  disabled at inference for speed.

## 1. The decision unit (train = inference format)

Every training example and every inference call is one decision:

```
system:  fixed player-agent prompt + action schema
user:    [FACTS]  deterministic RAG context for this exact state
         [STATE]  GameState::prompt_text() output
         [GOAL]   current objective string
assistant: <think>Staryu is Water; ThunderShock is 2x. It outspeeds me at low HP.</think>
           {"action":"fight","move":"THUNDERSHOCK"}
```

Train/inference parity is a hard requirement: the dataset builder and the serving
context builder share one implementation of [FACTS].

## 2. RAG: deterministic keyed retrieval (no vector DB)

Game state is structured, so retrieval is exact, not fuzzy:
- in battle → enemy species entry (types/stats), our moveset with type multipliers
  vs that enemy (computed from `assets/json/typechart.json` + `moves.json`)
- overworld → current map's encounter table (`encounters.json`), warps/connections
  (from map objects/headers), current WALKTHROUGH.md section for the active goal
- always → party summary facts (learnset next-moves, evolution levels)

Implementation: `training/context.py` (Python, used by dataset builder + serving
shim). Budget ≈ 400–700 tokens of facts per call.

## 3. Data pipeline (`training/`)

Sources, ranked by volume:
1. **Synthetic with ground-truth labels** (~70%):
   - navigation: sample (map,pos,goal) → optimal step from BFS over `.blk` maps
     (reuses the proven collision model: bottom-left tile in coll set, top tile ≠ 0x29)
   - battle move choice: enumerate (enemy, moveset) pairs → argmax damage proxy
     (power × STAB × type multiplier, accuracy-weighted); label = best move
   - battle meta: low-HP → heal/switch decisions from templated states
   - fact usage: Q&A grounded in FACTS block (encounters, learnsets, evolutions)
2. **Claude-teacher rollouts** (~25%): Claude plays via gb-agent; each decision
   logged with reasoning; **rejection-sampled** by RAM-verified outcomes (badge/level/
   position progress, enemy HP down, no blackout). Only progress-positive steps kept.
3. **Journal mining** (~5%): existing `journal/session-*` events filtered to
   successful trajectories (Mt Moon correct route, won battles, catches).
4. **Recovery pairs**: documented failure states (menu traps, 0-PP move, blackout
   risk) → correct recovery action.

Builder: `training/build_dataset.py` → versioned `training/data/vN/{train,val,test}.jsonl`
in Qwen3 chat format, stratified across navigation / battle-move / battle-meta /
menu / fact-usage, near-dup filtered, with a `stats.json` report.

## 4. Training

- **Stack**: HF transformers + PEFT, plain **LoRA in bf16** (no bitsandbytes — the
  4-bit stack is the shakiest dependency on new Blackwell+ARM silicon). torchtune is
  the fallback. First task is a smoke run to validate the stack on GB10.
- **Recipe (8B)**: LoRA r=32 α=64 dropout 0.05 on attn+MLP projections; lr 1e-4
  cosine; 2–3 epochs; seq 2048; effective batch ≈ 64 via grad accumulation. bf16
  full-precision base (~16GB) + activations fits easily in 128GB unified.
- **Artifacts**: adapter → merged model → GGUF convert → `ollama create pokered-8b`.
- 30B-A3B repeat once the 8B pipeline is proven.

## 5. Evaluation

- **Offline** (`training/eval_offline.py`): held-out test set — valid-JSON rate,
  exact action match, battle-move optimality vs ground truth, nav-step optimality.
  Baselines: stock Qwen3-8B with the same RAG prompt (and gpt-oss:120b for reference).
- **Live**: scripted scenario suite from saved checkpoints (`run/ck-*.state`):
  win a set battle, navigate A→B, execute a heal trip, grind safely. RAM-verified
  pass/fail via existing peeks. Same harness, so results are directly comparable.

## 6. Serving

`gb-agent play --backend custom`: planner calls a thin shim (Python or Rust port of
context.py) that builds FACTS+STATE+GOAL, hits Ollama (`pokered-8b`), parses the
action JSON. Existing enforced-JSON + scrape fallback and no-progress guards stay.

## 7. Repo layout

```
training/
  context.py        # deterministic RAG context builder (shared)
  synth/nav.py battle.py facts.py recovery.py
  mine_journals.py  # journal → curated examples
  rollout_format.py # Claude-teacher rollouts → examples
  build_dataset.py  # assemble/stratify/dedup/split → data/vN/
  train_lora.py     # PEFT bf16 LoRA
  eval_offline.py
  README.md         # runbook: env setup, build, train, eval, serve
```

## 8. Risks

- **GB10 training-stack maturity**: mitigated by bf16 LoRA (no bnb), latest PyTorch
  CUDA wheels for ARM (NVIDIA's index if needed), torchtune fallback, early smoke run.
- **Small-model fact hallucination**: mitigated by RAG-first design and fact-usage
  training examples that teach copying from FACTS.
- **Distribution shift** (model reaches states teacher never saw): mitigated by
  recovery pairs, no-progress guards in harness, and iterating dataset versions with
  rollouts from the *student* corrected by the teacher (DAgger-style) in v2.
