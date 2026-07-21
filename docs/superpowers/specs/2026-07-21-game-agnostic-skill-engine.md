# Game-Agnostic Skill Engine — design spec

**Goal:** drop in *any* Game Boy ROM and build a model that plays it, via three
interchangeable skill sources, then distill and run — with a dead-simple CLI. Execute
as part of the queued restructure (after v3). See memory `queued-repo-restructure`.

## One idea: three teachers → one data pool → one distill
All three modes emit the SAME training example format `(FACTS+STATE+GOAL → action, think)`
into `games/<game>/data/`. `distill` trains on whatever's been collected — mix freely.

| Mode | CLI | Teacher / signal | When to use |
|---|---|---|---|
| **User-driven** | `play` | YOU play the TUI; we record state→your action (behavioral cloning) | fastest bootstrap; demonstrates scripted gates |
| **Teacher-aided (AI)** | `guide` | model plays; an **automated LLM-teacher** labels the correct action (runs unattended, no human) — cheap rule-based ground truth first, LLM-teacher for the rest (DAgger / policy distillation) | fill gaps on states the model actually reaches |
| **Walkthrough-autonomous** | `learn` | ingest a public guide → grounded subgoal/reward checklist → reward-filtered self-imitation | hands-off grinding once a plan exists |

## Architecture (game-agnostic core + per-game adapter)
```
gb-tui/
├── README.md                 # quickstart (the 6 commands below)
├── gbskill                   # single CLI dispatcher (thin argparse over pipeline/)
├── src/                      # Rust gb-agent — already any-GB-game
├── pipeline/                 # game-AGNOSTIC core
│   ├── adapter.py            # GameAdapter ABC (below)
│   ├── play_record.py        # (1) human TUI play -> action-abstracted demos
│   ├── guide_fleet.py        # (2) DAgger fleet: model + teacher labels
│   ├── walkthrough_ingest.py # guide file -> grounded subgoal/reward checklist
│   ├── learn_selfplay.py     # (3) reward-filtered self-imitation fleet
│   ├── reward.py             # generic reward (exploration + counter/flag deltas)
│   ├── autoplay.py fleet.py  # shared model-in-the-loop runner
│   ├── build_dataset.py train_lora.py make_ggufs.sh serve_shim.py eval_*.py
├── games/
│   └── pokemon_red/
│       ├── adapter.py        # implements GameAdapter (= today executor+context+navigate)
│       ├── knowledge/        # facts (assets/json + gamedata)
│       ├── subgoals.json     # from walkthrough_ingest (grounded checklist)
│       ├── data/             # collected training examples (all 3 modes)  [gitignored]
│       └── README.md WALKTHROUGH.md
└── models/                   # trained ggufs per game  [gitignored]
```

### GameAdapter interface (one file per game)
```python
class GameAdapter(ABC):
    action_space: list[str]
    def snapshot(self, raw_ram) -> dict            # RAM -> structured state
    def state_text(self, state) -> str             # STATE block
    def build_facts(self, state) -> str            # retrieved FACTS (RAG)
    def objective(self, state, subgoals) -> str    # current GOAL (walkthrough-aware)
    def execute(self, action, state) -> str        # high-level action -> button script
    def abstract(self, buttons, before, after) -> dict | None  # buttons -> high-level action (for `play`)
    def reward(self, before, after) -> float       # default: generic; override per game
```
Adding a game = write this one file + drop in knowledge. Everything else is generic.

### `guide` — automated LLM-teacher (default, unattended)
The trained (small) model plays the fleet. At each decision the teacher label is chosen:
1. **Rule-based ground truth first** where derivable (type/matchup, HP-threshold, known warp) — cheap, no LLM call.
2. Else query an **automated LLM-teacher** (a *stronger* model — configurable: a bigger
   local model e.g. Qwen3-30B-A3B, or a frontier API) with the same `FACTS+STATE+GOAL`
   (plus the walkthrough subgoals for extra context) → it returns the correct high-level
   action + a short `think`. Student≠teacher → correction; student==teacher → confirmation.
Runs headless (no human in the loop). This is classic distillation-from-a-larger-teacher:
the teacher only fires at decision points, and its judgments are baked into the small model
by `distill`. Teacher model set via `--teacher <model>` (config default per game).

### Reward (`reward.py`, generic default)
`+` new map/area/state visited (exploration/novelty); `+` score/money/party/inventory/XP up;
`+` persistent story flag flipped; `+` next `subgoals.json` item satisfied (dense signal);
`−` died/blacked-out; `−` wedged in one spot N steps. Games override `reward()` if they can
do better; the walkthrough checklist supplies the dense per-milestone bonus.

### Walkthrough ingest (legal-safe)
- Reads a **user-provided local file** (does NOT scrape). User obtains the guide.
- LLM distills it into `subgoals.json` = grounded checklist `{objective, trigger_location,
  done_condition}` — we keep the STRUCTURE, not verbatim prose.
- Prefer CC-licensed sources (Bulbapedia CC-BY-NC-SA, Fandom/Wikipedia CC-BY-SA) if anything
  is committed/attributed. Proprietary guides (GameFAQs/IGN) stay local inference-time only;
  never trained-into published weights nor committed.

## The 6-command UX (README quickstart)
```bash
./gbskill init  <game> --rom <rom.gb>     # scaffold games/<game>/ (adapter stub + knowledge)
./gbskill play  <game>                    # 1) YOU play in the TUI; records training data
./gbskill guide <game>                    # 2) AI-teacher-corrected fleet; records corrections
./gbskill learn <game> --guide <file>     # 3) walkthrough -> reward -> autonomous fleet
./gbskill distill <game>                  # data -> LoRA -> q8_0 + q4_K_M ggufs, imported to Ollama
./gbskill run   <game>                    # watch the trained model play (uses q4 by default)
```
`gbskill status <game>` shows collected-example counts, model versions, fleet state.
Any of `play`/`guide`/`learn` can run in any order and any number of times before `distill`;
they all append to the same `games/<game>/data/` pool. Loop `learn → distill → learn` to
self-improve; sprinkle `play`/`guide` on the parts the fleet can't crack.

## Notes
- Pokémon Red is the reference adapter; its executor/context/navigate move behind the ABC.
- `distill` always builds BOTH quants (make_ggufs.sh) per the standing rule.
- Keep the published model free of third-party guide text (structure-only from walkthroughs).
