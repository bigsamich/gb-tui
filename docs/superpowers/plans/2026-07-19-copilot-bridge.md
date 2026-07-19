# Copilot Bridge & Knowledge Base Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In-game AI copilot (hints + autopilot) backed by local Ollama, plus a journal/dataset pipeline shared by gb-tui and gb-agent.

**Architecture:** Four new modules (`gamestate`, `journal`, `copilot`, `autopilot`) in the existing crate. The emu thread publishes typed `GameState` snapshots; the copilot thread talks to Ollama over HTTP; the autopilot executes deterministic op-scripts through a new `EmuCommand::RunOps`; everything logs to JSONL.

**Tech Stack:** existing crate + `serde`, `serde_json`, `ureq` (HTTP), `toml` (config) — all pure Rust.

## Global Constraints

- Pure Rust; no C deps beyond existing ALSA.
- Copilot strictly additive: Ollama down → gameplay unaffected, one-line notice.
- Pokémon Red specific (`gamestate` hardcodes Red US RAM addresses).
- Map data (.blk/.bst) fetched by script into gitignored `run/maps/`; never committed.
- Checkpoint fixtures (`run/ck-*.state`) and ROM are gitignored; tests using them skip when absent (same pattern as the Pokémon Red integration test).
- `cargo fmt` + `cargo clippy --all-targets -- -D warnings` clean at every commit.

## File Structure

```
src/gamestate.rs        GameState reader + name tables + prompt/JSON rendering
src/journal.rs          JSONL session logs + screenshots
src/copilot.rs          Ollama client (stream + blocking), config, system prompt
src/mapdata.rs          .blk/.bst decoding, walkability grids, BFS
src/autopilot/mod.rs    Driver (macro executor over EmuHandle) + macros
src/autopilot/planner.rs Action enum, LLM output parser, planner loop
src/emu.rs              (modify) RunOps command, GameState publishing, peek in trait
src/core/mod.rs         (modify) default peek in EmulatorCore trait
src/ui/app.rs           (modify) copilot panel, hint/autopilot modes, input journaling
src/bin/agent.rs        (modify) --journal flag, export subcommand
run/fetch-maps.sh       downloads map assets from pret/pokered
docs/training.md        LoRA + ollama create recipe (docs only)
```

---

### Task 1: `gamestate` — typed RAM reader

**Files:** Create `src/gamestate.rs`; modify `src/lib.rs` (`pub mod gamestate;`), `src/core/mod.rs`, `src/core/gb.rs`. Add deps: `cargo add serde --features derive` and `cargo add serde_json`.

**Interfaces:**
- Produces: `EmulatorCore::peek(&self, addr: u16) -> u8` (trait method, default returns 0; GbCore overrides with existing `GbCore::peek`).
- Produces: `gamestate::GameState` (all fields `pub`, `#[derive(Clone, Debug, Serialize)]`):
  `map: u8, map_name: String, x: u8, y: u8, money: u32, badges: u8, party: Vec<PartyMon>, battle: Option<BattleState>, bag: Vec<BagItem>, menu_cursor: u8`
  with `PartyMon { species: u8, name: String, level: u8, hp: u16, max_hp: u16, status: u8, moves: Vec<MoveSlot> }`, `MoveSlot { id: u8, name: String, pp: u8 }`, `BattleState { kind: u8, enemy_species: u8, enemy_name: String, enemy_level: u8, enemy_hp: u16 }`, `BagItem { id: u8, name: String, count: u8 }`.
- Produces: `GameState::read(core: &dyn EmulatorCore) -> GameState`, `GameState::prompt_text(&self) -> String`, `GameState::to_json(&self) -> serde_json::Value`.

- [ ] **Step 1: Failing test** — `#[cfg(test)]` in `src/gamestate.rs`:

```rust
#[test]
fn reads_boulder_badge_checkpoint() {
    let rom = std::path::Path::new("test-roms/pokemon-red.gb");
    let state = std::path::Path::new("run/ck-BOULDER-BADGE.state");
    if !rom.exists() || !state.exists() {
        eprintln!("SKIP: fixtures absent");
        return;
    }
    let mut core = crate::core::gb::GbCore::new();
    core.load_rom(&std::fs::read(rom).unwrap(), None).unwrap();
    core.load_state(&std::fs::read(state).unwrap()).unwrap();
    let gs = GameState::read(&core);
    assert_eq!(gs.map, 54); // Pewter Gym
    assert_eq!(gs.badges, 1);
    assert_eq!(gs.party.len(), 2);
    assert_eq!(gs.party[0].name, "CHARMELEON");
    assert_eq!(gs.party[0].level, 18);
    assert_eq!(gs.party[1].name, "PIKACHU");
    assert!(gs.prompt_text().contains("CHARMELEON L18"));
    assert!(gs.money > 0);
}
```

- [ ] **Step 2:** `cargo test gamestate` → FAIL (module missing).
- [ ] **Step 3: Implement.** RAM map (verified this session): party count `D163`; species list `D164`; mon structs base `D16B` stride 44 (`+1/+2` HP BE, `+4` status, `+8..=11` move ids, `+0x1D..=0x20` PP, `+0x21` level, `+0x22/+0x23` max HP BE); player pos `D362`/`D361`; map `D35E`; badges `D356`; money BCD `D347..D349` (`hi*10000 + mid*100 + lo` with BCD nibbles); bag `D31D` count then id/qty pairs terminated by `0xFF`; battle kind `D057` (0=none,1=wild,2=trainer); enemy species `CFE5`, HP `CFE6/7` BE, level `CFF3`; menu cursor `CC26`. Name tables as `fn species_name(id: u8) -> String` (match with entries for at least: Rhydon 0x01, Nidoran♂ 0x03, Clefairy 0x04, Spearow 0x05, Nidoran♀ 0x0F, Onix 0x22, Pidgey 0x24, Mankey 0x39, Pikachu 0x54, Sandshrew 0x60, Jigglypuff 0x64, Zubat 0x6B, Ekans 0x6C, Paras 0x6D, Weedle 0x70, Kakuna 0x71, Beedrill 0x72, Caterpie 0x7B, Metapod 0x7C, Butterfree 0x7D, Bulbasaur 0x99, Rattata 0xA5, Geodude 0xA9, Charmander 0xB0, Squirtle 0xB1, Charmeleon 0xB2, Wartortle 0xB3, Charizard 0xB4; fallback `format!("SPECIES_{:02X}", id)`), `fn move_name(id: u8)` (Pound 1, Scratch 10, Gust 16, Tackle 33, Tail Whip 39, Poison Sting 40, Leer 43, Growl 45, Ember 52, Flamethrower 53, String Shot 81, Thundershock 84, Thunder Wave 86, Quick Attack 98, Harden 106, Defense Curl 111, Screech 103, Bide 117; fallback `MOVE_{}`), `fn item_name(id: u8)` (Poké Ball 0x04, Antidote 0x0B, Potion 0x14, TM34 0xC?, Oak's Parcel 0x46, Town Map 0x05, Escape Rope 0x1D; fallback `ITEM_{:02X}`), `fn map_name(id: u8)` (0 Pallet Town, 1 Viridian City, 2 Pewter City, 12 Route 1, 13 Route 2, 37 Bobby's house 1F, 38 Bobby's house 2F, 39 rival's house, 40 Oak's Lab, 41 Viridian Pokécenter, 42 Viridian Mart, 47 Forest N gate, 50 Forest S gate, 51 Viridian Forest, 54 Pewter Gym, 56 Pewter Mart, 58 Pewter Pokécenter; fallback `MAP_{}`). `prompt_text()` renders e.g.:
  `Location: Pewter Gym (map 54) at (4,2). Money: 1713. Badges: 1.` / per mon `CHARMELEON L18 24/53HP [Scratch 35PP, Growl 30PP, Ember 18PP, Leer 30PP]` / battle line when active `IN BATTLE (trainer) vs ONIX L14 3HP` / bag line.
  Trait change in `src/core/mod.rs`: add `fn peek(&self, _addr: u16) -> u8 { 0 }` to `EmulatorCore`; in `src/core/gb.rs` move the existing inherent `peek` into the trait impl.
- [ ] **Step 4:** `cargo test gamestate` → PASS (or SKIP without fixtures — run with fixtures present locally).
- [ ] **Step 5:** fmt, clippy, `git commit -m "feat: typed Pokemon Red GameState reader"`.

### Task 2: `journal` — JSONL session logs

**Files:** Create `src/journal.rs`; modify `src/lib.rs`.

**Interfaces:**
- Produces: `journal::Journal::create(base: &Path) -> anyhow::Result<Journal>` (makes `base/<UTC ISO stamp>/`), `Journal::log(&mut self, source: Source, frame: u64, state: serde_json::Value, kind: EventKind)` (appends one line, flushes, never panics — errors to stderr), `Journal::save_screenshot(&mut self, rgb: &[u8], w: u32, h: u32) -> Option<String>` (PNG via existing `png` crate, returns filename), `Journal::dir(&self) -> &Path`.
- Produces: `Source { Human, Copilot, Autopilot, Agent }` and
```rust
#[derive(Serialize)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum EventKind {
    Input { buttons: String },
    Exchange { question: String, answer: String, screenshot: Option<String> },
    Decision { goal: String, action: String, outcome: String, state_after: serde_json::Value },
    Note { text: String },
}
```
Envelope written per line: `{"ts":<unix secs>,"frame":N,"source":"human",...state, flattened kind}` via a private `#[derive(Serialize)] struct Envelope { ts: u64, frame: u64, source: Source, state: Value, #[serde(flatten)] kind: EventKind }`.

- [ ] **Step 1: Failing test:** create journal in tempdir, log an `Input` and an `Exchange`, read the file back, `serde_json::from_str` each line, assert `event` fields and that 2 lines exist; `save_screenshot` writes a decodable PNG (encode 2×2 RGB, assert file exists and starts with PNG magic).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit `feat: JSONL gameplay journal`.

### Task 3: `copilot` — Ollama client + config + system prompt

**Files:** Create `src/copilot.rs`; modify `src/lib.rs`. Deps: `cargo add ureq toml`.

**Interfaces:**
- Produces: `copilot::Config { pub ollama_url: String, pub model: String, pub vision: bool, pub journal_dir: PathBuf }` with `Config::load() -> Config` (reads `gb-tui.toml` beside cwd if present, else defaults: `http://localhost:11434`, `qwen2.5:14b`, `vision=false`, `journal`).
- Produces: `copilot::HintRequest { pub state_text: String, pub recent: String, pub question: String, pub image_png: Option<Vec<u8>> }`.
- Produces: `copilot::Copilot::spawn(cfg: Config) -> Copilot` with `Copilot::ask_streaming(&self, req: HintRequest)` (sends to worker thread) and `Copilot::poll(&self) -> Option<CopilotMsg>` where `CopilotMsg { Chunk(String), Done(String), Error(String) }` (`Done` carries the full answer).
- Produces: `copilot::ask_blocking(cfg: &Config, system: &str, user: String) -> anyhow::Result<String>` (non-streaming, for the planner).
- Produces: `copilot::SYSTEM_PROMPT: &str` — the Gen-1 expert persona. Write it verbatim in the code (summary of required content): role statement ("expert Pokémon Red assistant watching a live game; answer concisely for a player mid-session"), strategy knowledge from PLAYBOOK: Gen-1 special stat is single (Onix/Geodude Special 30 → special attacks beat them despite type resist), Growl-stacking trivializes physical attackers, poison ticks 1HP/4 steps overworld and can black out, carry Antidotes in Viridian Forest, Ember 2× vs bugs, DSum patterns exist for rare encounters, centers heal PP too, ledges are one-way south, and answer format guidance ("2-5 sentences, concrete next action first").

**Ollama protocol:** POST `{url}/api/chat` body `{"model":..., "messages":[{"role":"system","content":...},{"role":"user","content":..., "images":[base64]?}], "stream":true}`; each response line is JSON `{"message":{"content":"..."},"done":false}`; accumulate until `done:true`. Non-streaming: `"stream":false`, single JSON. Base64 via a tiny local encoder fn (no new dep; implement standard alphabet).

- [ ] **Step 1: Failing test:** start `std::net::TcpListener` on port 0 in a thread; accept one connection; read request; write an HTTP/1.1 200 with three JSONL body lines (`{"message":{"content":"Use "},"done":false}`, `{"message":{"content":"Ember"},"done":false}`, `{"message":{"content":""},"done":true}`); `Copilot::spawn` against `http://127.0.0.1:{port}`, `ask_streaming`, poll until `Done(full)`, assert `full == "Use Ember"`. Second test: `Config::load` defaults when no file. Third: base64 of `b"hi"` == `"aGk="`.
- [ ] **Step 2:** FAIL. **Step 3:** implement (worker thread + `ureq::post(...).send_json(...)` reading the response body line-by-line with `BufRead`). **Step 4:** PASS. **Step 5:** commit `feat: Ollama copilot client with streaming`.

### Task 4: `mapdata` — collision decode + BFS

**Files:** Create `src/mapdata.rs`, `run/fetch-maps.sh`; modify `src/lib.rs`, `.gitignore` (`run/maps/`).

**Interfaces:**
- Produces: `mapdata::MapGrid { pub w: usize, pub h: usize }` with `MapGrid::walkable(&self, x: u8, y: u8) -> bool`.
- Produces: `mapdata::load(map_id: u8, assets_dir: &Path) -> Option<MapGrid>` — supported ids table:
  `0 Pallet (10×9 blocks, overworld), 1 Viridian (20×18, overworld), 2 Pewter (20×18, overworld), 12 Route 1 (10×18, overworld), 13 Route 2 (10×36, overworld), 51 Forest (17×24, forest)`.
- Produces: `mapdata::bfs(grid: &MapGrid, start: (u8,u8), goal: (u8,u8)) -> Option<Vec<(char, u8)>>` returning run-length compressed moves (`('u', 3)` = 3 steps up), and `mapdata::moves_to_ops(path: &[(char,u8)]) -> String` producing agent-script tokens (`"up:48 wait:10 ..."`, 16 frames/step).

**Decoding (port of the proven Python solver):** block map `.blk` (one byte per block) × blockset `.bst` (16 tile ids per block, 4×4); step (x,y) walkable iff tile at `(2x, 2y+1)` ∈ collision set. `Overworld_Coll = {0x00,0x10,0x1B,0x20,0x21,0x23,0x2C,0x2D,0x2E,0x30,0x31,0x33,0x39,0x3C,0x3E,0x52,0x54,0x58,0x5B}`; `Forest_Coll = {0x1E,0x20,0x2E,0x30,0x34,0x37,0x39,0x3A,0x40,0x51,0x52,0x5A,0x5C,0x5E,0x5F}`.

`run/fetch-maps.sh`: curls from `https://raw.githubusercontent.com/pret/pokered/master/` the files `maps/{PalletTown,ViridianCity,PewterCity,Route1,Route2,ViridianForest}.blk` and `gfx/blocksets/{overworld,forest}.bst` into `run/maps/`.

- [ ] **Step 1: Failing tests** (skip when `run/maps` absent): load map 51, assert `w==34 && h==48`, `walkable(1,18)` (BC3's corridor) true, `walkable(0,0)` false; bfs (15,19)→(1,0) returns Some with total steps ≥ 40; `moves_to_ops(&[('u',3),('l',2)]) == "up:48 wait:10 left:32 wait:10"`.
- [ ] **Step 2:** FAIL. **Step 3:** implement + fetch script (`chmod +x`). **Step 4:** run `./run/fetch-maps.sh` then PASS. **Step 5:** commit `feat: map collision decoding and BFS pathing`.

### Task 5: emu RunOps + GameState publishing + autopilot Driver/macros

**Files:** Create `src/autopilot/mod.rs`; modify `src/emu.rs`, `src/lib.rs`, `src/bin/agent.rs` (reuse `Op`/`parse_script` by moving them into `src/emu.rs` and re-importing in agent).

**Interfaces:**
- Produces (emu): move `Op` + `parse_script(&str) -> anyhow::Result<Vec<Op>>` + `exec_ops` logic from `src/bin/agent.rs` into `src/emu.rs` (public); agent binary imports them. New `EmuCommand::RunOps(Vec<Op>)` — the emu thread executes queued ops frame-by-frame inside its paced loop (holding buttons for the op's frame count, honoring audio pacing) instead of reading UI button commands; publishes `SharedState.ops_active: bool`. New `SharedState.game_state: Option<GameState>` refreshed every 30 frames and immediately when an op batch completes.
- Produces (autopilot): 
```rust
pub enum MacroResult { Done, BattleStarted, Aborted, Failed(String) }
pub struct Driver { ctl: EmuController, pub abort: Arc<AtomicBool>, pub assets: PathBuf }
// EmuController (add in src/emu.rs THIS task): cloneable handle with
//   EmuHandle::controller(&self) -> EmuController { tx: Sender<EmuCommand>, shared: Arc<Mutex<SharedState>> }
//   EmuController::send(&self, EmuCommand), EmuController::shared(&self) -> Arc<Mutex<SharedState>>
impl Driver {
    pub fn new(ctl: EmuController, abort: Arc<AtomicBool>, assets: PathBuf) -> Driver;
    pub fn state(&self) -> GameState;                  // waits for a fresh snapshot
    pub fn run_ops(&self, script: &str) -> MacroResult; // RunOps + wait for ops_active=false, checking abort
    pub fn fight(&self) -> MacroResult;      // battle rounds until in_battle==0 (cap 12): per round: normalize (b,b,up,left), open menu, read menu_cursor from state, navigate to best damaging move with PP (prefer super-effective by tiny type table: Fire>Bug/Grass, Electric>Water/Flying, Normal neutral; else first with PP), fire, advance
    pub fn flee(&self) -> MacroResult;
    pub fn use_item(&self, item: &str) -> MacroResult;  // field or battle item by bag position
    pub fn walk_to(&self, x: u8, y: u8) -> MacroResult; // mapdata::load current map; BFS; execute in ≤10-step chunks, re-checking state (battle → BattleStarted)
    pub fn heal_at_center(&self) -> MacroResult;        // known counter approach for maps 41/58 + heal dialogue ops; Failed elsewhere
    pub fn interact(&self) -> MacroResult;              // a:8
    pub fn press(&self, seq: &str) -> MacroResult;      // validated via parse_script
}
```

- [ ] **Step 1: Failing tests** (in `src/autopilot/mod.rs`, skip without ROM+fixtures): headless — build `EmuHandle` with `frame_duration: Duration::from_millis(1)` and a `GbCore` loaded with `run/ck-PIKACHU-encounter.state` (a live wild battle): `driver.flee()` returns `Done` and final state has `battle: None`. Second: from `ck-BOULDER-BADGE.state`, `driver.press("a:8 wait:60")` is `Done` and `state()` returns map 54. Third: `parse_script("up:16 mash-a:2")` still works from its new home (unit).
- [ ] **Step 2:** FAIL. **Step 3:** implement emu changes then Driver (macros are op-scripts + state polling — port the session's proven sequences: normalize `b:8 wait:20 b:8 wait:20 up:4 wait:12 left:4 wait:12`, menu open `a:8 wait:70`, per-slot `down:4 wait:14`, fire `a:8 wait:360 a:8 wait:140`, flee cursor `down:4 wait:16 right:4 wait:16 a:8 wait:220`). **Step 4:** PASS with fixtures. **Step 5:** commit `feat: emu op execution + autopilot driver macros`.

### Task 6: planner — action parsing + loop

**Files:** Create `src/autopilot/planner.rs`.

**Interfaces:**
- Produces: `Action { Fight, Flee, UseItem(String), WalkTo(u8,u8), HealAtCenter, Interact, Press(String), Stop(String) }` with `parse_action(text: &str) -> Option<Action>` — accepts a JSON object anywhere in the text `{"action":"walk_to","x":10,"y":5}` (find first `{`, last `}`, `serde_json` parse; unknown → None).
- Produces: `PlannerEvent { Decided { action: String, outcome: String }, Message(String), Finished(String) }` and
  `run_planner(cfg: &copilot::Config, driver: &Driver, journal: Arc<Mutex<Journal>>, goal: String, events: Sender<PlannerEvent>, abort: Arc<AtomicBool>)` — loop ≤50 steps: build prompt (SYSTEM_PROMPT + action vocabulary description + goal + `state.prompt_text()`), `ask_blocking`, parse (on None → one corrective retry appending "Reply with ONLY a JSON action object."; then Stop), execute via Driver, journal a `Decision`, emit event; terminate on Stop/abort/cap. Never emits SAVE-menu or release sequences (`press` scripts containing `start:` longer than 8 frames are rejected — the validator refuses `start` tokens entirely in planner mode).

- [ ] **Step 1: Failing tests:** `parse_action(r#"I think {"action":"fight"} is best"#) == Some(Fight)`; `walk_to` with x/y; garbage → None; planner loop with a fake blocking-ask (inject via a test-only `PlanBackend` trait — `run_planner` generic over `Fn(&str, String) -> Result<String>`) returning `{"action":"stop","reason":"done"}` → emits `Finished`, journals one Decision.
- [ ] **Step 2:** FAIL. **Step 3:** implement (make `run_planner` take `ask: impl Fn(&str, String) -> anyhow::Result<String>` so tests inject; production passes a closure over `ask_blocking`). **Step 4:** PASS. **Step 5:** commit `feat: autopilot planner loop`.

### Task 7: TUI integration

**Files:** Modify `src/ui/app.rs`, `src/ui/status.rs` (panel title shows copilot state), `src/emu.rs` if needed.

**Behavior (all in `App`):**
- New fields: `copilot: Copilot`, `cfg: copilot::Config`, `journal: Arc<Mutex<Journal>>`, `panel_log: Vec<(String,String)>` (role, text), `mode: UiMode { Play, HintInput(String), GoalInput(String), Autopilot { abort: Arc<AtomicBool>, rx: Receiver<PlannerEvent> } }`, `pending_answer: Option<String>`.
- `?` in Play → send `TogglePause` if unpaused, switch to `HintInput("")`; printable chars append, Backspace pops, Esc cancels (unpause), Enter → build `HintRequest { state_text: shared.game_state.prompt_text(), recent: last 20 journal lines read from file, question (empty → "What should I do here?"), image_png: encode shared rgb if cfg.vision }`, `ask_streaming`, mode back to Play; poll in `tick()`: `Chunk` appends to the streaming entry in `panel_log`; `Done(full)` journals `Exchange`.
- `Tab` in Play → `GoalInput("")`; Enter spawns a thread: `Driver::new(&handle…)` — note `EmuHandle` isn't `Sync`; instead the planner thread gets its own channel clones: refactor `EmuHandle` to make `tx: Sender<EmuCommand>` and `shared` cloneable handles (`EmuHandle::controller(&self) -> EmuController` with `send`, `shared`), Driver takes `EmuController`. Any key while `Autopilot` sets `abort`; `PlannerEvent`s append to `panel_log`; `Finished` returns mode to Play.
- Human input journaling: every accepted game-key press in Play mode logs `Input { buttons }` with the current `game_state` JSON (throttle: skip if same button within 200ms).
- Panel: right side widens to 34 cols when mode ≠ Play or `panel_log` non-empty; renders last N wrapped lines + a `[copilot: thinking…]` spinner while streaming; status panel gains one line `AI: idle|thinking|autopilot`.
- Ollama unreachable → `CopilotMsg::Error(e)` renders as panel line `Ollama not running — try: ollama serve`.

- [ ] **Step 1: tests** — pure helpers only: extract `fn wrap_panel_lines(log: &[(String,String)], width: u16) -> Vec<String>` into `src/ui/status.rs` and unit-test wrapping + role prefixes; `UiMode` transitions tested via a small `fn next_mode(mode, key) -> UiMode` pure function where practical.
- [ ] **Step 2-4:** implement; `cargo test` green; **manual smoke:** `ollama pull qwen2.5:14b` (or any installed model configured in `gb-tui.toml`), run TUI with Pokémon Red, press `?`, ask "how do I beat Brock", see streamed answer; press Tab, goal "win this battle" from a battle state, watch autopilot.
- [ ] **Step 5:** commit `feat: in-game copilot panel with hints and autopilot`.

### Task 8: agent journal flag + export + training docs

**Files:** Modify `src/bin/agent.rs`; create `docs/training.md`.

**Interfaces:**
- `gb-agent … --journal <dir>`: wraps the normal run; after executing the script, appends one `Note { text: format!("script: {script}") }` event with source `Agent` and final-state JSON.
- `gb-agent export --journal <dir> --format advice|policy --out <file>`:
  - `advice`: for each `exchange` event emit `{"conversations":[{"from":"system","value":SYSTEM_PROMPT},{"from":"human","value":"{state prompt_text}\n\n{question}"},{"from":"gpt","value":answer}]}` (state reconstructed from the logged JSON via `GameState` deserialization — add `Deserialize` derives in Task 1's types now: include `#[derive(Deserialize)]` from the start).
  - `policy`: for each `decision` emit `{"prompt":"{state}\nGOAL: {goal}","completion":action}`; for each human `input` emit `{"prompt":state,"completion":buttons}`.
- `docs/training.md`: dataset locations, export commands, LoRA recipe outline (llama-factory or unsloth on the GB10, base = the configured Ollama model's HF equivalent, then `ollama create gb-copilot -f Modelfile` with `FROM base + ADAPTER lora`), and how to point `gb-tui.toml` at `gb-copilot`.

- [ ] **Step 1: Failing test** (integration `tests/export.rs`): build a journal dir in tempdir with 1 exchange + 1 decision + 1 input line (hand-written JSON), run the export functions (`pub fn export_advice/export_policy` in a new `src/export.rs` so they're testable; agent subcommand calls them), assert output line counts (advice 1, policy 2) and JSON shape.
- [ ] **Step 2:** FAIL. **Step 3:** implement `src/export.rs` + agent arg parsing. **Step 4:** PASS. **Step 5:** commit `feat: journal export to fine-tune formats + training docs`; final `cargo test && cargo clippy --all-targets -- -D warnings && cargo build --release`.
