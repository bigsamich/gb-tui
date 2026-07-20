//! Autopilot: deterministic gameplay macros executed through the emu thread,
//! ported from the proven shell protocols in run/PLAYBOOK.md.

pub mod planner;

use crate::emu::{EmuCommand, EmuController, parse_script};
use crate::gamestate::GameState;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

#[derive(Debug, PartialEq)]
pub enum MacroResult {
    Done,
    BattleStarted,
    Aborted,
    Failed(String),
}

pub struct Driver {
    ctl: EmuController,
    pub abort: Arc<AtomicBool>,
    pub assets: PathBuf,
}

const OPS_TIMEOUT: Duration = Duration::from_secs(90);

impl Driver {
    pub fn new(ctl: EmuController, abort: Arc<AtomicBool>, assets: PathBuf) -> Driver {
        Driver { ctl, abort, assets }
    }

    /// Latest published game state (waits briefly for the first snapshot).
    pub fn state(&self) -> GameState {
        let shared = self.ctl.shared();
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if let Some(gs) = shared.lock().unwrap().game_state.clone() {
                return gs;
            }
            if Instant::now() > deadline {
                return GameState {
                    map: 0,
                    map_name: "unknown".into(),
                    x: 0,
                    y: 0,
                    money: 0,
                    badges: 0,
                    party: vec![],
                    battle: None,
                    bag: vec![],
                    menu_cursor: 0,
                };
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    /// Queue an op script and wait for completion (or abort/timeout).
    pub fn run_ops(&self, script: &str) -> MacroResult {
        let ops = match parse_script(script) {
            Ok(o) => o,
            Err(e) => return MacroResult::Failed(format!("bad script: {e}")),
        };
        let shared = self.ctl.shared();
        let before = shared.lock().unwrap().ops_done;
        self.ctl.send(EmuCommand::RunOps(ops));
        let deadline = Instant::now() + OPS_TIMEOUT;
        loop {
            if self.abort.load(Ordering::SeqCst) {
                self.ctl.send(EmuCommand::AbortOps);
                return MacroResult::Aborted;
            }
            {
                let s = shared.lock().unwrap();
                if s.ops_done > before && !s.ops_active {
                    return MacroResult::Done;
                }
            }
            if Instant::now() > deadline {
                self.ctl.send(EmuCommand::AbortOps);
                return MacroResult::Failed("op timeout".into());
            }
            std::thread::sleep(Duration::from_millis(25));
        }
    }

    /// Pick the best damaging move slot (0-based). Prefers Ember-class fire
    /// (proven strongest vs early-game Special stats), then any damaging move
    /// with PP remaining.
    fn best_move_slot(&self, gs: &GameState) -> Option<usize> {
        let mon = gs.party.first()?;
        const DAMAGING: &[u8] = &[52, 53, 84, 98, 10, 33, 16, 40, 1];
        // Preference order: Flamethrower, Ember, Thundershock, Quick Attack,
        // Scratch, Tackle, Gust, Poison Sting, Pound.
        const PREF: &[u8] = &[53, 52, 84, 98, 10, 33, 16, 40, 1];
        for want in PREF {
            if let Some(idx) = mon
                .moves
                .iter()
                .position(|m| m.id == *want && m.pp > 0 && DAMAGING.contains(&m.id))
            {
                return Some(idx);
            }
        }
        None
    }

    /// One cursor-verified battle round. Returns Done while the battle
    /// continues; the caller loops via `fight`. Progress is verified against
    /// RAM (HP changes) rather than fixed waits, because battle text length
    /// varies wildly (level-ups, effectiveness lines, multi-mon trainers).
    fn battle_round(&self) -> MacroResult {
        // Advance text with B (B advances but never selects), normalize to the
        // battle menu, cursor to FIGHT, open the move menu.
        let r = self.run_ops(
            "wait:8 b:8 wait:140 b:8 wait:100 b:8 wait:30 up:4 wait:12 left:4 wait:12 a:8 wait:80",
        );
        if r != MacroResult::Done {
            return r;
        }
        let gs = self.state();
        let Some(before) = gs.battle.clone() else {
            return MacroResult::Done;
        };
        let our_hp_before = gs.party.first().map(|m| m.hp).unwrap_or(0);
        let Some(target) = self.best_move_slot(&gs) else {
            return MacroResult::Failed("no damaging move with PP".into());
        };
        // Cursor-independent selection: the move menu does not wrap, so three
        // Ups clamp to slot 0 from anywhere, then walk down to the target.
        let mut nav = String::from("up:4 wait:14 up:4 wait:14 up:4 wait:14 ");
        for _ in 0..target {
            nav.push_str("down:4 wait:14 ");
        }
        let r = self.run_ops(&format!("{nav}a:8 wait:380"));
        if r != MacroResult::Done {
            return r;
        }
        // Push text forward until something observable changed (either side's
        // HP, the enemy species, or battle end) — up to ~12 presses.
        for _ in 0..12 {
            let now = self.state();
            let Some(b) = &now.battle else {
                return MacroResult::Done;
            };
            let our_hp = now.party.first().map(|m| m.hp).unwrap_or(0);
            if b.enemy_hp != before.enemy_hp
                || b.enemy_species != before.enemy_species
                || our_hp != our_hp_before
            {
                // Round resolved; clear any trailing text (B: safe, never selects).
                let _ = self.run_ops("b:8 wait:120");
                return MacroResult::Done;
            }
            let r = self.run_ops("b:8 wait:150");
            if r != MacroResult::Done {
                return r;
            }
        }
        MacroResult::Done
    }

    /// Fight until the battle ends (cap 15 rounds).
    pub fn fight(&self) -> MacroResult {
        for _ in 0..15 {
            if self.abort.load(Ordering::SeqCst) {
                return MacroResult::Aborted;
            }
            if self.state().battle.is_none() {
                // Clear any lingering victory text (B never selects).
                return self.run_ops("b:8 wait:120 b:8 wait:60");
            }
            match self.battle_round() {
                MacroResult::Done => continue,
                other => return other,
            }
        }
        MacroResult::Failed("battle did not end in 15 rounds".into())
    }

    /// Escape a wild battle (3 attempts).
    pub fn flee(&self) -> MacroResult {
        for _ in 0..3 {
            if self.abort.load(Ordering::SeqCst) {
                return MacroResult::Aborted;
            }
            if self.state().battle.is_none() {
                return MacroResult::Done;
            }
            let r = self.run_ops(
                "a:8 wait:140 b:8 wait:30 b:8 wait:30 up:4 wait:12 left:4 wait:12 \
                 down:4 wait:16 right:4 wait:16 a:8 wait:240 a:8 wait:120",
            );
            if r != MacroResult::Done {
                return r;
            }
        }
        if self.state().battle.is_none() {
            MacroResult::Done
        } else {
            MacroResult::Failed("could not flee".into())
        }
    }

    /// Use a bag item by (partial, case-insensitive) name. In battle this goes
    /// through the battle ITEM menu; in the field through the Start menu.
    pub fn use_item(&self, item: &str) -> MacroResult {
        let gs = self.state();
        let needle = item.to_lowercase();
        let Some(pos) = gs
            .bag
            .iter()
            .position(|i| i.name.to_lowercase().contains(&needle))
        else {
            return MacroResult::Failed(format!("no '{item}' in bag"));
        };
        let downs: String = "down:4 wait:14 ".repeat(pos);
        if gs.battle.is_some() {
            self.run_ops(&format!(
                "a:8 wait:140 b:8 wait:30 b:8 wait:30 up:4 wait:12 left:4 wait:12 \
                 down:4 wait:16 a:8 wait:80 {downs}a:8 wait:80 a:8 wait:160 a:8 wait:320 a:8 wait:150"
            ))
        } else {
            self.run_ops(&format!(
                "start:8 wait:60 down:4 wait:14 down:4 wait:14 a:8 wait:60 \
                 {downs}a:8 wait:50 a:8 wait:60 a:8 wait:120 a:8 wait:100 \
                 b:8 wait:40 b:8 wait:40 b:8 wait:40"
            ))
        }
    }

    /// BFS-walk to (x, y) on the current map. Executes in chunks, re-checking
    /// state between chunks; a random battle returns `BattleStarted`.
    pub fn walk_to(&self, x: u8, y: u8) -> MacroResult {
        let mut blocked: Vec<(u8, u8)> = Vec::new();
        let mut last_pos: Option<(u8, u8)> = None;
        for _ in 0..30 {
            if self.abort.load(Ordering::SeqCst) {
                return MacroResult::Aborted;
            }
            let gs = self.state();
            if let Some(b) = &gs.battle {
                // Handle wild interruptions inline (flee); only trainer
                // battles need the planner's attention.
                if b.kind == 1 {
                    match self.flee() {
                        MacroResult::Done => continue,
                        // Cornered by a faster wild: fight it out inline.
                        MacroResult::Failed(_) => match self.fight() {
                            MacroResult::Done => continue,
                            other => return other,
                        },
                        other => return other,
                    }
                }
                return MacroResult::BattleStarted;
            }
            if (gs.x, gs.y) == (x, y) {
                return MacroResult::Done;
            }
            let Some(grid) = crate::mapdata::load(gs.map, &self.assets) else {
                return MacroResult::Failed(format!(
                    "no map data for {} (map {}) — run run/fetch-maps.sh",
                    gs.map_name, gs.map
                ));
            };
            let Some(path) = crate::mapdata::bfs(&grid, (gs.x, gs.y), (x, y), &blocked) else {
                return MacroResult::Failed(format!(
                    "no path from ({},{}) on map {} (grid {}x{}) to ({x},{y})",
                    gs.x, gs.y, gs.map, grid.w, grid.h
                ));
            };
            if path.is_empty() {
                return MacroResult::Done;
            }
            // Execute at most ~10 steps per chunk, then re-check.
            let mut chunk = Vec::new();
            let mut steps = 0u32;
            for (d, n) in path {
                if steps >= 10 {
                    break;
                }
                let take = (n as u32).min(10 - steps) as u8;
                chunk.push((d, take));
                steps += take as u32;
            }
            let r = self.run_ops(&crate::mapdata::moves_to_ops(&chunk));
            if r != MacroResult::Done {
                return r;
            }
            // If we didn't move, something invisible to the map (an NPC)
            // blocks the next tile. Try interacting first (a blocking trainer
            // engages; a sign closes harmlessly), then mark and re-route.
            let now = self.state();
            if last_pos == Some((now.x, now.y)) {
                let r = self.run_ops("a:8 wait:180 b:8 wait:60");
                if r != MacroResult::Done {
                    return r;
                }
                if self.state().battle.is_some() {
                    return MacroResult::BattleStarted;
                }
                if let Some((d, _)) = chunk.first() {
                    let (bx, by) = match d {
                        'u' => (now.x, now.y.wrapping_sub(1)),
                        'd' => (now.x, now.y.wrapping_add(1)),
                        'l' => (now.x.wrapping_sub(1), now.y),
                        _ => (now.x.wrapping_add(1), now.y),
                    };
                    if blocked.len() < 8 && !blocked.contains(&(bx, by)) {
                        blocked.push((bx, by));
                    }
                }
            }
            last_pos = Some((now.x, now.y));
        }
        MacroResult::Failed("walk did not converge".into())
    }

    /// Heal at a Pokémon Center (must already be inside one: maps 41/58).
    pub fn heal_at_center(&self) -> MacroResult {
        let gs = self.state();
        if ![41, 58, 64, 68].contains(&gs.map) {
            return MacroResult::Failed(format!(
                "not inside a Pokemon Center (in {})",
                gs.map_name
            ));
        }
        self.run_ops(
            "up:64 wait:16 a:8 wait:120 a:8 wait:120 a:8 wait:450 a:8 wait:120 \
             a:8 wait:120 a:8 wait:100 b:8 wait:60",
        )
    }

    /// Encode the current framebuffer as PNG (for failure-escalation shots).
    pub fn screenshot_png(&self) -> Option<(Vec<u8>, u32, u32)> {
        let shared = self.ctl.shared();
        let s = shared.lock().unwrap();
        if s.width == 0 || s.rgb.len() < s.width * s.height * 3 {
            return None;
        }
        let (w, h) = (s.width as u32, s.height as u32);
        let mut out = Vec::new();
        {
            let mut enc = png::Encoder::new(std::io::Cursor::new(&mut out), w, h);
            enc.set_color(png::ColorType::Rgb);
            enc.set_depth(png::BitDepth::Eight);
            enc.write_header().ok()?.write_image_data(&s.rgb).ok()?;
        }
        Some((out, w, h))
    }

    /// Face forward and interact (signs, NPCs, pickups).
    pub fn interact(&self) -> MacroResult {
        self.run_ops("a:8 wait:120 a:8 wait:120")
    }

    /// Raw validated op script (planner escape hatch). Scripts containing
    /// `start` are rejected — the planner must not open the save menu.
    pub fn press(&self, seq: &str) -> MacroResult {
        if seq.split_whitespace().any(|t| t.starts_with("start")) {
            return MacroResult::Failed("start button not allowed in press".into());
        }
        match parse_script(seq) {
            Ok(_) => self.run_ops(seq),
            Err(e) => MacroResult::Failed(format!("bad press script: {e}")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::EmulatorCore;
    use crate::core::gb::GbCore;
    use crate::emu::{EmuConfig, EmuHandle};

    fn fixture_driver(state_file: &str) -> Option<(EmuHandle, Driver)> {
        let rom = std::path::Path::new("test-roms/pokemon-red.gb");
        let state = std::path::Path::new(state_file);
        if !rom.exists() || !state.exists() {
            eprintln!("SKIP: fixtures absent");
            return None;
        }
        let mut core = GbCore::new();
        core.load_rom(&std::fs::read(rom).unwrap(), None).unwrap();
        core.load_state(&std::fs::read(state).unwrap()).unwrap();
        let handle = EmuHandle::spawn(
            Box::new(core),
            EmuConfig {
                frame_duration: std::time::Duration::from_micros(200),
                ring: None,
                rom_path: None,
                autosave: None,
            },
        );
        let driver = Driver::new(
            handle.controller(),
            Arc::new(AtomicBool::new(false)),
            std::path::PathBuf::from("run/maps"),
        );
        Some((handle, driver))
    }

    #[test]
    fn flee_escapes_wild_battle() {
        let Some((handle, driver)) = fixture_driver("run/ck-PIKACHU-encounter.state") else {
            return;
        };
        // Enter the battle proper first (encounter text still pending).
        driver.run_ops("a:8 wait:150 a:8 wait:150 a:8 wait:150");
        let r = driver.flee();
        assert_eq!(r, MacroResult::Done, "flee failed");
        assert!(driver.state().battle.is_none());
        handle.stop();
    }

    #[test]
    fn press_runs_and_rejects_start() {
        let Some((handle, driver)) = fixture_driver("run/ck-BOULDER-BADGE.state") else {
            return;
        };
        assert_eq!(driver.press("a:8 wait:30"), MacroResult::Done);
        assert!(matches!(driver.press("start:8"), MacroResult::Failed(_)));
        assert_eq!(driver.state().map, 54);
        handle.stop();
    }
}
