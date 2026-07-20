use crate::audio::AudioRing;
use crate::core::{Button, EmulatorCore};
use crate::gamestate::GameState;
use crate::persist::{BatterySaver, state_path};
use anyhow::{Context, Result, anyhow, bail};
use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

/// One scripted input operation (shared by gb-agent and the autopilot).
#[derive(Clone, Debug, PartialEq)]
pub enum Op {
    Hold(Button, u32),
    Wait(u32),
    MashA(u32),
}

/// Parse an input script like `"up:16 a:8 wait:30 mash-a:5"`.
pub fn parse_script(script: &str) -> Result<Vec<Op>> {
    let mut ops = Vec::new();
    for tok in script.split_whitespace() {
        let (name, n) = tok
            .split_once(':')
            .ok_or_else(|| anyhow!("bad token (want name:count): {tok}"))?;
        let n: u32 = n.parse().with_context(|| format!("bad count in {tok}"))?;
        let op = match name.to_ascii_lowercase().as_str() {
            "up" => Op::Hold(Button::Up, n),
            "down" => Op::Hold(Button::Down, n),
            "left" => Op::Hold(Button::Left, n),
            "right" => Op::Hold(Button::Right, n),
            "a" => Op::Hold(Button::A, n),
            "b" => Op::Hold(Button::B, n),
            "start" => Op::Hold(Button::Start, n),
            "select" => Op::Hold(Button::Select, n),
            "wait" => Op::Wait(n),
            "mash-a" => Op::MashA(n),
            other => bail!("unknown op: {other}"),
        };
        ops.push(op);
    }
    Ok(ops)
}

/// Button changes to apply before running one frame.
#[derive(Clone, Debug)]
struct FrameAct {
    pre: Vec<(Button, bool)>,
}

/// Expand ops into per-frame actions (press/hold/release/settle semantics
/// identical to gb-agent's headless executor).
fn expand_ops(ops: &[Op]) -> VecDeque<FrameAct> {
    let mut out = VecDeque::new();
    let none = || FrameAct { pre: vec![] };
    for op in ops {
        match op {
            Op::Hold(b, n) => {
                out.push_back(FrameAct {
                    pre: vec![(*b, true)],
                });
                for _ in 1..*n {
                    out.push_back(none());
                }
                out.push_back(FrameAct {
                    pre: vec![(*b, false)],
                });
                out.push_back(none()); // 2 settle frames total
            }
            Op::Wait(n) => {
                for _ in 0..*n {
                    out.push_back(none());
                }
            }
            Op::MashA(n) => {
                for _ in 0..*n {
                    out.push_back(FrameAct {
                        pre: vec![(Button::A, true)],
                    });
                    out.push_back(none());
                    out.push_back(FrameAct {
                        pre: vec![(Button::A, false)],
                    });
                    for _ in 0..15 {
                        out.push_back(none());
                    }
                }
            }
        }
    }
    out
}

#[derive(Debug)]
pub enum EmuCommand {
    Button(Button, bool),
    TogglePause,
    Turbo(bool),
    FrameStep,
    SaveState(u8),
    LoadState(u8),
    /// Queue scripted ops for frame-accurate execution inside the emu loop.
    RunOps(Vec<Op>),
    /// Drop any queued/active ops (autopilot abort).
    AbortOps,
    /// Write a save state to an explicit path (headless play sessions).
    SaveStateTo(PathBuf),
    Stop,
}

#[derive(Debug)]
pub enum EmuEvent {
    Toast(String),
    Error(String),
}

#[derive(Default)]
pub struct SharedState {
    pub rgb: Vec<u8>,
    pub width: usize,
    pub height: usize,
    pub seq: u64,
    pub fps: f32,
    pub paused: bool,
    pub turbo: bool,
    /// Typed game state, refreshed periodically and at op-batch completion.
    pub game_state: Option<GameState>,
    /// True while scripted ops are executing.
    pub ops_active: bool,
    /// Completed op-batch counter (for run_ops completion detection).
    pub ops_done: u64,
}

pub struct EmuConfig {
    pub frame_duration: Duration,
    pub ring: Option<Arc<AudioRing>>,
    pub rom_path: Option<PathBuf>,
}

impl Default for EmuConfig {
    fn default() -> Self {
        Self {
            // 59.7275 Hz
            frame_duration: Duration::from_nanos(16_742_706),
            ring: None,
            rom_path: None,
        }
    }
}

/// Cloneable control handle for threads other than the UI (autopilot, planner).
#[derive(Clone)]
pub struct EmuController {
    tx: Sender<EmuCommand>,
    shared: Arc<Mutex<SharedState>>,
}

impl EmuController {
    pub fn send(&self, cmd: EmuCommand) {
        let _ = self.tx.send(cmd);
    }

    pub fn shared(&self) -> Arc<Mutex<SharedState>> {
        Arc::clone(&self.shared)
    }
}

pub struct EmuHandle {
    tx: Sender<EmuCommand>,
    events: Receiver<EmuEvent>,
    shared: Arc<Mutex<SharedState>>,
    join: JoinHandle<()>,
}

impl EmuHandle {
    pub fn spawn(core: Box<dyn EmulatorCore>, cfg: EmuConfig) -> Self {
        let (tx, rx) = mpsc::channel::<EmuCommand>();
        let (ev_tx, ev_rx) = mpsc::channel::<EmuEvent>();
        let shared = Arc::new(Mutex::new(SharedState::default()));
        let shared2 = Arc::clone(&shared);
        let join = std::thread::spawn(move || run_loop(core, cfg, rx, ev_tx, shared2));
        Self {
            tx,
            events: ev_rx,
            shared,
            join,
        }
    }

    pub fn send(&self, cmd: EmuCommand) {
        let _ = self.tx.send(cmd);
    }

    pub fn events(&self) -> &Receiver<EmuEvent> {
        &self.events
    }

    pub fn shared(&self) -> Arc<Mutex<SharedState>> {
        Arc::clone(&self.shared)
    }

    pub fn controller(&self) -> EmuController {
        EmuController {
            tx: self.tx.clone(),
            shared: Arc::clone(&self.shared),
        }
    }

    /// Stops the thread and joins it; the loop flushes battery RAM on exit.
    pub fn stop(self) {
        let _ = self.tx.send(EmuCommand::Stop);
        let _ = self.join.join();
    }
}

const STATE_REFRESH_FRAMES: u64 = 30;

fn run_loop(
    mut core: Box<dyn EmulatorCore>,
    cfg: EmuConfig,
    rx: Receiver<EmuCommand>,
    events: Sender<EmuEvent>,
    shared: Arc<Mutex<SharedState>>,
) {
    let mut paused = false;
    let mut turbo = false;
    let mut step_once = false;
    let mut audio_buf: Vec<i16> = Vec::with_capacity(4096);
    let mut saver = cfg.rom_path.as_deref().map(BatterySaver::new);
    let mut next_deadline = Instant::now() + cfg.frame_duration;
    let mut fps_window_start = Instant::now();
    let mut fps_frames: u32 = 0;
    let mut fps = 0.0f32;
    let mut frame_count: u64 = 0;
    let mut op_frames: VecDeque<FrameAct> = VecDeque::new();

    'outer: loop {
        // Drain pending commands (block while paused with nothing to do).
        loop {
            let cmd = if paused && !step_once {
                match rx.recv_timeout(Duration::from_millis(50)) {
                    Ok(c) => Some(c),
                    Err(mpsc::RecvTimeoutError::Timeout) => None,
                    Err(mpsc::RecvTimeoutError::Disconnected) => break 'outer,
                }
            } else {
                match rx.try_recv() {
                    Ok(c) => Some(c),
                    Err(mpsc::TryRecvError::Empty) => None,
                    Err(mpsc::TryRecvError::Disconnected) => break 'outer,
                }
            };
            let Some(cmd) = cmd else { break };
            match cmd {
                EmuCommand::Button(b, pressed) => core.set_button(b, pressed),
                EmuCommand::TogglePause => {
                    paused = !paused;
                    next_deadline = Instant::now() + cfg.frame_duration;
                }
                EmuCommand::Turbo(t) => {
                    turbo = t;
                    if let Some(ring) = &cfg.ring {
                        ring.clear();
                    }
                    next_deadline = Instant::now() + cfg.frame_duration;
                }
                EmuCommand::FrameStep => step_once = true,
                EmuCommand::RunOps(ops) => {
                    op_frames.extend(expand_ops(&ops));
                    shared.lock().unwrap().ops_active = true;
                }
                EmuCommand::AbortOps => {
                    op_frames.clear();
                    // Release everything a script might have left held.
                    for b in [
                        Button::Up,
                        Button::Down,
                        Button::Left,
                        Button::Right,
                        Button::A,
                        Button::B,
                        Button::Start,
                        Button::Select,
                    ] {
                        core.set_button(b, false);
                    }
                    let mut s = shared.lock().unwrap();
                    s.ops_active = false;
                    s.ops_done += 1;
                }
                EmuCommand::SaveStateTo(path) => {
                    let msg = core
                        .save_state()
                        .map_err(|e| e.to_string())
                        .and_then(|d| std::fs::write(&path, d).map_err(|e| e.to_string()))
                        .map(|_| format!("state saved to {}", path.display()));
                    let _ = events.send(match msg {
                        Ok(m) => EmuEvent::Toast(m),
                        Err(e) => EmuEvent::Error(e),
                    });
                }
                EmuCommand::SaveState(slot) => {
                    let msg = match &cfg.rom_path {
                        None => Err("no ROM path for save state".to_string()),
                        Some(p) => core
                            .save_state()
                            .map_err(|e| e.to_string())
                            .and_then(|d| {
                                std::fs::write(state_path(p, slot), d).map_err(|e| e.to_string())
                            })
                            .map(|_| format!("State saved to slot {slot}")),
                    };
                    let _ = events.send(match msg {
                        Ok(m) => EmuEvent::Toast(m),
                        Err(e) => EmuEvent::Error(e),
                    });
                }
                EmuCommand::LoadState(slot) => {
                    let msg = match &cfg.rom_path {
                        None => Err("no ROM path for load state".to_string()),
                        Some(p) => std::fs::read(state_path(p, slot))
                            .map_err(|_| format!("slot {slot} is empty"))
                            .and_then(|d| core.load_state(&d).map_err(|e| e.to_string()))
                            .map(|_| format!("State loaded from slot {slot}")),
                    };
                    let _ = events.send(match msg {
                        Ok(m) => EmuEvent::Toast(m),
                        Err(e) => EmuEvent::Error(e),
                    });
                }
                EmuCommand::Stop => break 'outer,
            }
        }

        {
            let mut s = shared.lock().unwrap();
            s.paused = paused;
            s.turbo = turbo;
            s.fps = fps;
        }
        if paused && !step_once {
            continue;
        }
        step_once = false;

        // Apply any scripted button changes for this frame.
        if let Some(act) = op_frames.pop_front() {
            for (b, pressed) in act.pre {
                core.set_button(b, pressed);
            }
            if op_frames.is_empty() {
                let mut s = shared.lock().unwrap();
                s.ops_active = false;
                s.ops_done += 1;
                s.game_state = Some(GameState::read(core.as_ref()));
            }
        }

        core.run_frame();
        frame_count += 1;
        fps_frames += 1;
        if fps_window_start.elapsed() >= Duration::from_secs(1) {
            fps = fps_frames as f32 / fps_window_start.elapsed().as_secs_f32();
            fps_frames = 0;
            fps_window_start = Instant::now();
        }

        // Publish frame.
        {
            let frame = core.framebuffer();
            let mut s = shared.lock().unwrap();
            s.rgb = frame.rgb;
            s.width = frame.width;
            s.height = frame.height;
            s.seq += 1;
            if frame_count.is_multiple_of(STATE_REFRESH_FRAMES) {
                s.game_state = Some(GameState::read(core.as_ref()));
            }
        }

        // Audio + pacing.
        audio_buf.clear();
        core.drain_audio(&mut audio_buf);
        match (&cfg.ring, turbo) {
            (Some(ring), false) => {
                // Blocking push is the clock.
                ring.push_blocking(&audio_buf, Duration::from_millis(500));
            }
            (Some(_), true) => {} // turbo: drop audio, no wait
            (None, false) => {
                // Muted: timer pacing.
                let now = Instant::now();
                if next_deadline > now {
                    std::thread::sleep(next_deadline - now);
                }
                next_deadline += cfg.frame_duration;
                if next_deadline < Instant::now() {
                    next_deadline = Instant::now() + cfg.frame_duration;
                }
            }
            (None, true) => {}
        }

        if let Some(saver) = &mut saver {
            let _ = saver.maybe_flush(core.battery_ram().as_deref(), false);
        }
    }

    // Final battery flush on the way out.
    if let Some(saver) = &mut saver {
        let _ = saver.maybe_flush(core.battery_ram().as_deref(), true);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::{Button, EmulatorCore, Frame, RomInfo};
    use anyhow::Result;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::Duration;

    struct FakeCore {
        frames: Arc<AtomicU64>,
        presses: Arc<AtomicU64>,
        state: Vec<u8>,
    }

    impl EmulatorCore for FakeCore {
        fn load_rom(&mut self, _: &[u8], _: Option<&[u8]>) -> Result<RomInfo> {
            unreachable!("emu thread never loads roms")
        }
        fn run_frame(&mut self) {
            self.frames.fetch_add(1, Ordering::SeqCst);
        }
        fn framebuffer(&self) -> Frame {
            let n = self.frames.load(Ordering::SeqCst) as u8;
            Frame {
                width: 2,
                height: 2,
                rgb: vec![n; 12],
            }
        }
        fn set_button(&mut self, _: Button, pressed: bool) {
            if pressed {
                self.presses.fetch_add(1, Ordering::SeqCst);
            }
        }
        fn drain_audio(&mut self, out: &mut Vec<i16>) {
            out.extend([0i16; 64]);
        }
        fn sample_rate(&self) -> u32 {
            44100
        }
        fn save_state(&self) -> Result<Vec<u8>> {
            Ok(self.state.clone())
        }
        fn load_state(&mut self, d: &[u8]) -> Result<()> {
            assert_eq!(d, self.state.as_slice());
            Ok(())
        }
        fn battery_ram(&self) -> Option<Vec<u8>> {
            None
        }
    }

    fn fast_cfg() -> EmuConfig {
        EmuConfig {
            frame_duration: Duration::from_millis(1),
            ring: None,
            rom_path: None,
        }
    }

    fn spawn_fake() -> (EmuHandle, Arc<AtomicU64>, Arc<AtomicU64>) {
        let frames = Arc::new(AtomicU64::new(0));
        let presses = Arc::new(AtomicU64::new(0));
        let core = FakeCore {
            frames: Arc::clone(&frames),
            presses: Arc::clone(&presses),
            state: vec![7, 7, 7],
        };
        (
            EmuHandle::spawn(Box::new(core), fast_cfg()),
            frames,
            presses,
        )
    }

    #[test]
    fn runs_frames_and_publishes() {
        let (handle, frames, _) = spawn_fake();
        std::thread::sleep(Duration::from_millis(100));
        assert!(frames.load(Ordering::SeqCst) > 10);
        let shared = handle.shared();
        let s = shared.lock().unwrap();
        assert!(s.seq > 10);
        assert_eq!(s.rgb.len(), 12);
        assert!(s.game_state.is_some());
        drop(s);
        handle.stop();
    }

    #[test]
    fn pause_stops_frames_and_frame_step_advances_one() {
        let (handle, frames, _) = spawn_fake();
        handle.send(EmuCommand::TogglePause);
        std::thread::sleep(Duration::from_millis(50));
        let n1 = frames.load(Ordering::SeqCst);
        std::thread::sleep(Duration::from_millis(50));
        assert_eq!(
            frames.load(Ordering::SeqCst),
            n1,
            "paused but frames advanced"
        );
        assert!(handle.shared().lock().unwrap().paused);
        handle.send(EmuCommand::FrameStep);
        std::thread::sleep(Duration::from_millis(50));
        assert_eq!(frames.load(Ordering::SeqCst), n1 + 1);
        handle.stop();
    }

    #[test]
    fn stop_joins_cleanly() {
        let (handle, frames, _) = spawn_fake();
        std::thread::sleep(Duration::from_millis(20));
        handle.stop();
        let n = frames.load(Ordering::SeqCst);
        std::thread::sleep(Duration::from_millis(30));
        assert_eq!(frames.load(Ordering::SeqCst), n);
    }

    #[test]
    fn save_state_without_rom_path_emits_error_event() {
        let (handle, _, _) = spawn_fake();
        handle.send(EmuCommand::SaveState(1));
        std::thread::sleep(Duration::from_millis(50));
        let ev = handle.events().try_recv().expect("expected an event");
        assert!(matches!(ev, EmuEvent::Error(_)));
        handle.stop();
    }

    #[test]
    fn run_ops_executes_and_signals_done() {
        let (handle, _, presses) = spawn_fake();
        let ctl = handle.controller();
        let shared = ctl.shared();
        let before = shared.lock().unwrap().ops_done;
        ctl.send(EmuCommand::RunOps(parse_script("a:4 wait:10 a:4").unwrap()));
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            {
                let s = shared.lock().unwrap();
                if s.ops_done > before && !s.ops_active {
                    break;
                }
            }
            assert!(Instant::now() < deadline, "ops never completed");
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(presses.load(Ordering::SeqCst), 2); // two A presses
        handle.stop();
    }

    #[test]
    fn parse_script_round_trip() {
        let ops = parse_script("up:16 mash-a:2 wait:5").unwrap();
        assert_eq!(
            ops,
            vec![Op::Hold(Button::Up, 16), Op::MashA(2), Op::Wait(5)]
        );
        assert!(parse_script("fly:1").is_err());
    }
}
