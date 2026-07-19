use crate::audio::AudioRing;
use crate::core::{Button, EmulatorCore};
use crate::persist::{BatterySaver, state_path};
use std::path::PathBuf;
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

#[derive(Debug)]
pub enum EmuCommand {
    Button(Button, bool),
    TogglePause,
    Turbo(bool),
    FrameStep,
    SaveState(u8),
    LoadState(u8),
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

    /// Stops the thread and joins it; the loop flushes battery RAM on exit.
    pub fn stop(self) {
        let _ = self.tx.send(EmuCommand::Stop);
        let _ = self.join.join();
    }
}

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

        core.run_frame();
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
        fn set_button(&mut self, _: Button, _: bool) {}
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

    fn spawn_fake() -> (EmuHandle, Arc<AtomicU64>) {
        let frames = Arc::new(AtomicU64::new(0));
        let core = FakeCore {
            frames: Arc::clone(&frames),
            state: vec![7, 7, 7],
        };
        (EmuHandle::spawn(Box::new(core), fast_cfg()), frames)
    }

    #[test]
    fn runs_frames_and_publishes() {
        let (handle, frames) = spawn_fake();
        std::thread::sleep(Duration::from_millis(100));
        assert!(frames.load(Ordering::SeqCst) > 10);
        let shared = handle.shared();
        let s = shared.lock().unwrap();
        assert!(s.seq > 10);
        assert_eq!(s.rgb.len(), 12);
        drop(s);
        handle.stop();
    }

    #[test]
    fn pause_stops_frames_and_frame_step_advances_one() {
        let (handle, frames) = spawn_fake();
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
        let (handle, frames) = spawn_fake();
        std::thread::sleep(Duration::from_millis(20));
        handle.stop();
        let n = frames.load(Ordering::SeqCst);
        std::thread::sleep(Duration::from_millis(30));
        assert_eq!(frames.load(Ordering::SeqCst), n);
    }

    #[test]
    fn save_state_without_rom_path_emits_error_event() {
        let (handle, _) = spawn_fake();
        handle.send(EmuCommand::SaveState(1));
        std::thread::sleep(Duration::from_millis(50));
        let ev = handle.events().try_recv().expect("expected an event");
        assert!(matches!(ev, EmuEvent::Error(_)));
        handle.stop();
    }
}
