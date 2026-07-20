# gb-tui

A Game Boy / Game Boy Color emulator for your terminal, built in Rust with
[ratatui](https://ratatui.rs) and the [boytacean](https://crates.io/crates/boytacean) core.

## Requirements

- A truecolor terminal (any modern one)
- Linux: `libasound2-dev` (ALSA headers) to build audio support

## Run

    cargo run --release -- path/to/roms/      # ROM browser
    cargo run --release -- path/to/game.gb    # boot a ROM directly

For the best picture, zoom your terminal out (usually `Ctrl+-`) until the
window is at least **160×76** cells — the screen scales to whatever space it
has and shows a hint while it's below full resolution. Audio plays through
your default output device; terminals that support the kitty keyboard
protocol (kitty, WezTerm, foot) get true button hold/release handling, and
hold-Space turbo instead of toggle.

## Keys

| Key | Action | Key | Action |
| --- | --- | --- | --- |
| Arrows | D-pad | Space | Turbo |
| Z / X | B / A | P | Pause |
| Enter | Start | N | Frame step |
| Backspace | Select | F1–F4 | Save state |
| Esc | Back to browser | Shift+F1–F4 | Load state |
| Q | Quit | | |

Battery saves are written as `<rom>.sav` next to the ROM (compatible with
other emulators); save states as `<rom>.st1`–`.st4`. Core warnings go to
`$TMPDIR/gb-tui.stderr.log` so they never disturb the display.

## Tests

`cargo test` — includes a headless run of the committed, freely-licensed
[dmg-acid2](https://github.com/mattcurrie/dmg-acid2) test ROM. Drop your own
legally-dumped `test-roms/pokemon-red.gb` (gitignored) to enable the
Pokémon Red compatibility tests.

## Architecture

The frontend talks to the emulator only through an `EmulatorCore` trait
(`src/core/`), so a Game Boy Advance core can slot in later. Emulation runs
on its own thread, paced by audio back-pressure: it pushes samples into a
blocking ring buffer that the cpal output stream drains at 44.1 kHz. The
screen renders as `▀` half-block cells (two pixels per cell) with
aspect-preserving nearest-neighbor scaling.

## AI copilot (local)

With [Ollama](https://ollama.com) running locally (`ollama serve` + a pulled
model), the TUI gains an AI copilot:

- `?` — pause and ask about the current situation; the answer streams into the
  side panel (the model sees your live RAM-derived game state).
- `Tab` — hand the controls to the autopilot: a local LLM picks high-level
  actions (fight, flee, walk-to, heal…) executed by deterministic macros.
  Press any key to take back control.

Configure via `gb-tui.toml`:

```toml
ollama_url = "http://localhost:11434"
model = "qwen2.5:14b"
vision = false        # true for multimodal models (screenshot attached)
journal_dir = "journal"
```

All play (yours, the copilot's, the autopilot's) is journaled to
`journal/<session>/events.jsonl` and exportable as fine-tune datasets — see
`docs/training.md` for the fully-local training recipe. Autopilot walking needs
map data: `./run/fetch-maps.sh` once.

### Headless autonomous play

`gb-agent play` runs the same planner/macro loop without the TUI, at turbo
speed, with a switchable brain:

    gb-agent play --rom <rom> --state <file> --goal "win this battle" \
      --backend ollama            # local model (default, from gb-tui.toml)
    gb-agent play ... --backend claude   # plans via the claude CLI (your login)

No screenshots are taken unless necessary: the loop is RAM/text-only, and only
after two consecutive failed actions is one PNG saved and offered to the model.
A no-progress guard stops deterministically if the model loops. Sessions are
journaled like everything else.
