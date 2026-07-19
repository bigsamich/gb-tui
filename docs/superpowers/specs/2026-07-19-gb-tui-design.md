# gb-tui — Game Boy / Game Boy Color emulator TUI

**Date:** 2026-07-19
**Status:** Approved

## Summary

A terminal (ratatui) frontend for playing Game Boy and Game Boy Color ROMs, built on the
`boytacean` emulator core (crates.io, Apache-2.0). Real audio output via cpal. The frontend
talks to the core only through an `EmulatorCore` trait so a Game Boy Advance core
(`rustboyadvance-core`, git dependency) can be added later without frontend changes.

## Constraints

- **Pure Rust stack**: no C emulator cores, no cmake/clang build steps, no vendored C.
  All chosen crates (boytacean, ratatui, crossterm) are pure Rust; `cpal` is the sole
  OS-boundary crate (thin system bindings to ALSA/CoreAudio/WASAPI — required for real
  audio on any stack).
- **Primary test ROM: Pokémon Red** (user-supplied, legally owned; never committed).

## Goals

- Play commercial and homebrew GB/GBC ROMs in the terminal with sound.
- ROM browser, battery saves, 4 save-state slots, turbo/pause/frame-step, status panel.
- Run on any modern truecolor terminal; degrade gracefully on small terminals.

## Non-goals (v1)

- GBA support (architected for, not implemented).
- Link cable, RTC edge cases, Game Boy Printer, configurable key bindings.
- Sixel/kitty image-protocol rendering (half-blocks only in v1).

## Backend choice (research outcome)

- **GB/GBC: `boytacean` v0.12.x** — actively maintained, Apache-2.0, headless core with
  `load_rom`, `next_frame`, `frame_buffer_rgba` (160×144), `key_press`/`key_lift`,
  `audio_buffer` (stereo i16), and built-in save-state serialization. Passes
  dmg-acid2/cgb-acid2; MBC1/2/3/5.
- **GBA (later): `rustboyadvance-core`** from `michelhe/rustboyadvance-ng` (MIT, active,
  pure Rust) as a git dependency; needs an open-source replacement BIOS.
- Rejected: `safeboy`/SameBoy (GPL-3.0, beta, C toolchain), `mgba` bindings (v0.1.0,
  cmake/clang build), `rboy` (no crates.io releases), `padme-core` (abandoned, DMG-only).

## Architecture

Single binary crate `gb-tui`, four modules:

```
┌─────────────┐  commands (input, pause,   ┌──────────────────┐
│  UI thread   │  turbo, save-state)  ───►  │  Emulator thread  │
│  (ratatui)   │                            │  (boytacean core) │
│              │  ◄─── latest frame buffer  │                   │
└─────────────┘       (shared, lock-light)  └────────┬─────────┘
                                                     │ audio samples
                                              ┌──────▼─────┐
                                              │ cpal stream │ ── speakers
                                              └────────────┘
```

### `core` — emulator abstraction

```rust
trait EmulatorCore: Send {
    fn load_rom(&mut self, rom: &[u8], battery_ram: Option<&[u8]>) -> Result<RomInfo>;
    fn run_frame(&mut self);
    fn framebuffer(&self) -> Frame;            // RGBA bytes + width/height
    fn set_button(&mut self, b: Button, pressed: bool);
    fn drain_audio(&mut self, out: &mut Vec<i16>); // interleaved stereo
    fn sample_rate(&self) -> u32;
    fn save_state(&self) -> Result<Vec<u8>>;
    fn load_state(&mut self, data: &[u8]) -> Result<()>;
    fn battery_ram(&self) -> Option<Vec<u8>>;
}
```

`GbCore` wraps boytacean. The UI never touches boytacean types. A future `GbaCore`
implements the same trait; frontend selects by ROM extension.

### `emu` — emulation thread

- Owns the core. Receives `EmuCommand` (button events, pause, turbo, frame-step,
  save/load state, stop) over an mpsc channel.
- Publishes each finished frame into a shared latest-frame slot (mutex-guarded double
  buffer; UI reads at its own pace, never blocks emulation).
- Pacing: pushes audio into the ring buffer and waits when it is full — audio back-pressure
  locks emulation to real time. Turbo skips the wait (uncapped, audio drained/dropped).
  Pause stops frame production; frame-step advances exactly one frame while paused.

### `audio` — output

- cpal default output stream; lock-free SPSC ring buffer (~100 ms capacity).
- Underrun → output silence. No audio device → muted mode with a frame-timer clock
  (59.73 Hz) as pacing fallback and a status-panel warning.

### `ui` — ratatui app

- Screens: **RomBrowser** (scrollable list of `.gb`/`.gbc` files) and **Game**
  (game widget + status side panel). Toast overlays for transient messages.
- Status panel: ROM title, FPS, speed (1x/turbo/paused), audio state, key map.

## Rendering

- Custom widget draws `▀` half-blocks: fg color = top pixel, bg = bottom pixel
  (2 vertical pixels per cell), truecolor.
- Each frame, scale 160×144 to the largest size fitting the widget area preserving 10:9
  aspect, nearest-neighbor. At ≥160×72 cells rendering is pixel-perfect.
- Zoom hint: on startup/resize, if the grid is smaller than the ideal size, overlay a hint —
  e.g. "Zoom out (Ctrl+-) for full resolution — 112×50 of 160×72" — while still rendering
  scaled so the game stays playable. Hint disappears when the grid is large enough.

## Input

- Two-tier key handling:
  1. **Kitty keyboard protocol** where supported (crossterm enhancement flags): true
     press/release events.
  2. **Fallback auto-release**: a press (and OS auto-repeat) holds the button; no repeat
     within ~150 ms → release.
- Default map: arrows = D-pad, `Z` = B, `X` = A, `Enter` = Start, `Backspace` = Select,
  `Space` (hold) = turbo, `P` = pause, `N` = frame-step, `F1`–`F4` = save state,
  `Shift+F1`–`F4` = load state, `Esc` = back to browser, `Q` = quit.

## Persistence

- **CLI:** `gb-tui [dir|rom]` — ROM boots directly; directory (default cwd) opens browser.
- **Battery saves:** cartridge RAM as `<rom>.sav` next to the ROM (standard, portable
  format). Flushed on exit, on return to browser, and periodically (~every 10 s if dirty).
- **Save states:** boytacean's built-in state serialization, 4 slots as `<rom>.st1`–`.st4`
  next to the ROM.

## Error handling

- `anyhow` for fallible paths.
- Terminal guard (Drop + panic hook): always disable raw mode, leave alternate screen, pop
  kitty keyboard flags — a crash never garbles the shell.
- Bad/unsupported ROM → error toast in browser, not a crash.
- Audio init failure → muted + timer pacing, non-fatal.

## Testing

- Unit: scaler (aspect math, odd sizes, tiny areas), auto-release input logic, audio ring
  buffer.
- Integration: **Pokémon Red** is the primary test ROM. The test looks for the
  user-supplied copy at `test-roms/pokemon-red.gb` (gitignored) and skips with a notice if
  absent. It runs the ROM headless through `GbCore` for a few hundred frames, asserts the
  framebuffer is non-blank and stable (checksum), and exercises battery-save round-trip
  and save-state round-trip. Manual play-testing also targets Pokémon Red.
- Development follows TDD (superpowers:test-driven-development).

## Future work

- GBA via `rustboyadvance-core` behind `EmulatorCore` (BIOS bundling, 240×160 layout).
- Configurable key bindings; kitty/sixel image-protocol rendering; palette options for DMG.
