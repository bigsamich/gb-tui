# gb-tui Copilot Bridge & Gameplay Knowledge Base

**Date:** 2026-07-19
**Status:** Approved

## Summary

Bridge the human-facing TUI and the AI agent: an in-game copilot (hints on demand +
goal-driven autopilot) backed by a **local model via Ollama**, and a **journal** that
records all play — human, copilot, and autopilot — into a dataset suitable for
fine-tuning a fully local model later.

## Goals

- Press a key while playing to get AI advice about the current situation (game keeps
  working if Ollama is down — copilot is strictly additive).
- Hand the controls to an autopilot that plans with a local LLM and executes with
  deterministic macros; take control back with any keypress.
- Log every meaningful event (inputs, states, Q&A, decisions) in one JSONL format shared
  by `gb-tui` and `gb-agent`.
- Export the journal to standard fine-tune formats (advice chat pairs, policy pairs).
- Everything runs locally (Ollama on the user's GB10-class machine).

## Non-goals (this phase)

- Actually fine-tuning a model (follow-up project; recipe documented in `docs/training.md`).
- Vision-required operation (screenshots attach only when the configured model is
  multimodal; text/RAM-only is first-class).
- Frame-level button decisions by the LLM (macros own mechanics).
- Multi-game support beyond Pokémon Red (the `gamestate` module is Red-specific).

## Decisions (from brainstorm)

1. Help modes: **both** hints and autopilot.
2. Backend: **Ollama HTTP, local, from day one** (default `http://localhost:11434`).
3. Dataset: **decisions + demonstrations** (copilot exchanges AND human/agent play traces).
4. Autopilot: **LLM plans over a fixed action vocabulary; deterministic macros execute.**
5. Training scope: **collect + export now; train later** (documented recipe only).

## Architecture

Four new modules in the existing crate (in-process, no sidecar daemon):

- **`gamestate`** — typed reader of Pokémon Red RAM via `GbCore::peek` (map, x, y,
  party species/levels/HP/moves/PP, money, badges, bag, battle state, enemy
  species/level/HP, menu cursor). Renders to (a) compact prompt text, (b) JSON for logs.
  Addresses come from this session's proven peeks (documented in run/PLAYBOOK.md).
- **`copilot`** — background thread owning an Ollama client (`ureq`, pure Rust).
  `HintRequest { state, recent_events, question, screenshot: Option<Png> }` in;
  streamed reply chunks out over a channel. Config in `gb-tui.toml`:
  `ollama_url`, `model`, `vision: bool`, `journal_dir`.
- **`autopilot`** — macro library (ported from this session's shell protocols:
  cursor-verified battle round, flee, heal-at-center, item use, BFS walking over decoded
  `.blk` collision maps bundled for visited maps, interact, raw press) plus a planner
  loop: state → LLM picks ONE action → macro runs → repeat until goal/stop/abort/cap.
- **`journal`** — append-only session logs: `journal/<timestamp>/events.jsonl` +
  screenshots. Shared by `gb-tui` and `gb-agent`.

TUI integration: copilot panel (chat log + status) extends the status side panel.
Hotkeys: `?` = hint (auto-pauses, opens input line, Enter = default question),
`Tab` = autopilot (prompts for goal; default "make progress toward the next objective"),
any key = abort autopilot after current macro.

## Copilot request content

- System prompt: Gen-1 expert persona seeded with PLAYBOOK strategy knowledge
  (type-chart quirks, Growl-stacking, poison management, DSum hunting, menu pitfalls).
- GameState text summary; last ~20 journal events; user question.
- Screenshot attached only when `vision = true` in config.
- Replies stream token-by-token into the panel; exchange journaled; game stays paused
  until the user resumes.

## Autopilot action vocabulary

| Action | Macro |
|---|---|
| `fight` | cursor-verified battle round, best damaging move |
| `flee` | RUN escape sequence |
| `use_item {item}` | battle/field item use |
| `walk_to {x,y}` | BFS over map collision data |
| `heal_at_center` | full Pokémon Center interaction |
| `interact` | face + A |
| `press {seq}` | raw button escape hatch |
| `stop {reason}` | return control with explanation |

Safety: every decision journaled; any keypress aborts after current macro; step cap
(default 50); autopilot never uses SAVE or release/toss menus. Malformed LLM output →
one corrective retry, then `stop`.

## Journal format

`journal/<ISO-timestamp>/events.jsonl`, one typed event per line, shared envelope
`{ts, frame, source: human|copilot|autopilot|agent, state}`:

```jsonl
{"ts":1789,"frame":12345,"source":"human","event":"input","buttons":"a:8","state":{...}}
{"ts":1790,"frame":12400,"source":"copilot","event":"exchange","question":"...","answer":"...","screenshot":"00042.png","state":{...}}
{"ts":1795,"frame":12900,"source":"autopilot","event":"decision","goal":"...","action":"fight","outcome":"won","state_before":{...},"state_after":{...}}
```

Human inputs are logged per input chunk (state-change boundaries), not per frame.

## Dataset export

`gb-agent export --journal <dir> --format advice|policy`:

- **advice** → ShareGPT-style chat JSONL (system + state + question → answer) from
  copilot exchanges. For fine-tuning the hint model.
- **policy** → completion pairs (state summary + goal → action) from autopilot decisions
  and human demonstration chunks. For fine-tuning the playing model.

`docs/training.md` documents the follow-up recipe: LoRA fine-tune on the GB10, then
`ollama create` to serve the result under a model name the config can point at.

## Error handling

- Ollama unreachable → one-line panel notice ("Ollama not running — try `ollama serve`");
  gameplay unaffected.
- Journal write failure → logged to stderr file, never crashes play.
- Planner parse failure → corrective retry ×1, then `stop`.

## Testing

- Unit: GameState reader against saved checkpoint states (run/ck-*.state as fixtures);
  planner output parser; BFS walker vs decoded maps; journal serialization round-trip;
  exporter formats.
- Integration: mock Ollama HTTP server + scripted autopilot session headless from
  `ck-BOULDER-BADGE.state`.
- Manual: live hint and autopilot session against real Ollama.

## Future work

- The fine-tune itself (follow-up project once data accumulates).
- Sidecar extraction of copilot if other frontends appear.
- More decoded maps as the playthrough advances; auto-download/decode tooling.
