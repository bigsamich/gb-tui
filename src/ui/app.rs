use crate::audio::AudioOutput;
use crate::autopilot::Driver;
use crate::autopilot::planner::{PlannerEvent, run_planner};
use crate::copilot::{Config, Copilot, CopilotMsg, HintRequest, ask_blocking};
use crate::core::EmulatorCore;
use crate::core::gb::GbCore;
use crate::emu::{EmuCommand, EmuConfig, EmuEvent, EmuHandle};
use crate::input::{KeyTracker, map_key};
use crate::journal::{EventKind, Journal, Source};
use crate::persist;
use crate::ui::browser::Browser;
use crate::ui::screen::GameScreen;
use crate::ui::status::{StatusInfo, status_lines, wrap_panel_lines, zoom_hint};
use anyhow::Result;
use ratatui::DefaultTerminal;
use ratatui::crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::Line;
use ratatui::widgets::{Block, Borders, List, ListItem, ListState, Paragraph};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::Receiver;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const TOAST_TTL: Duration = Duration::from_secs(3);

struct GameSession {
    handle: EmuHandle,
    tracker: KeyTracker,
    title: String,
    cgb: bool,
    muted: bool,
    rom_path: PathBuf,
}

enum Screen {
    Browser(Browser),
    Game(Box<GameSession>),
}

enum UiMode {
    Play,
    HintInput(String),
    GoalInput(String),
    Autopilot {
        abort: Arc<AtomicBool>,
        rx: Receiver<PlannerEvent>,
    },
}

pub struct App {
    screen: Screen,
    audio: Option<AudioOutput>,
    kitty: bool,
    toasts: Vec<(String, Instant)>,
    quit: bool,
    needs_clear: bool,
    cfg: Config,
    copilot: Copilot,
    journal: Arc<Mutex<Journal>>,
    panel_log: Vec<(String, String)>,
    mode: UiMode,
    thinking: bool,
    pending_question: Option<String>,
    last_input_log: Instant,
}

impl App {
    pub fn run(terminal: &mut DefaultTerminal, start: PathBuf, kitty: bool) -> Result<()> {
        let audio = AudioOutput::try_new();
        let browser_dir = if start.is_file() {
            start.parent().unwrap_or(Path::new(".")).to_path_buf()
        } else {
            start.clone()
        };
        let cfg = Config::load();
        let journal = Arc::new(Mutex::new(Journal::create(&cfg.journal_dir)?));
        let copilot = Copilot::spawn(cfg.clone());
        let mut app = App {
            screen: Screen::Browser(Browser::new(browser_dir)),
            audio,
            kitty,
            toasts: Vec::new(),
            quit: false,
            needs_clear: false,
            cfg,
            copilot,
            journal,
            panel_log: Vec::new(),
            mode: UiMode::Play,
            thinking: false,
            pending_question: None,
            last_input_log: Instant::now(),
        };
        if start.is_file() {
            app.start_game(&start);
        }
        while !app.quit {
            app.tick();
            if app.needs_clear {
                terminal.clear()?;
                app.needs_clear = false;
            }
            terminal.draw(|f| app.draw(f))?;
            if event::poll(Duration::from_millis(8))? {
                let ev = event::read()?;
                app.handle_event(ev);
            }
        }
        app.end_game(); // flush battery via EmuHandle::stop
        Ok(())
    }

    fn toast(&mut self, msg: impl Into<String>) {
        self.toasts.push((msg.into(), Instant::now()));
    }

    fn start_game(&mut self, rom_path: &Path) {
        let rom = match std::fs::read(rom_path) {
            Ok(r) => r,
            Err(e) => return self.toast(format!("Read failed: {e}")),
        };
        let mut core = GbCore::new();
        let battery = persist::load_battery(rom_path);
        let info = match core.load_rom(&rom, battery.as_deref()) {
            Ok(i) => i,
            Err(e) => return self.toast(format!("Load failed: {e}")),
        };
        let ring = self.audio.as_ref().map(|a| std::sync::Arc::clone(&a.ring));
        let muted = ring.is_none();
        if let Some(ring) = &ring {
            ring.clear();
        }
        let handle = EmuHandle::spawn(
            Box::new(core),
            EmuConfig {
                ring,
                rom_path: Some(rom_path.to_path_buf()),
                ..Default::default()
            },
        );
        self.screen = Screen::Game(Box::new(GameSession {
            handle,
            tracker: KeyTracker::new(self.kitty),
            title: info.title,
            cgb: info.cgb,
            muted,
            rom_path: rom_path.to_path_buf(),
        }));
        self.needs_clear = true;
    }

    fn end_game(&mut self) {
        if let Screen::Game(session) = std::mem::replace(
            &mut self.screen,
            Screen::Browser(Browser::new(PathBuf::from("."))),
        ) {
            let dir = session
                .rom_path
                .parent()
                .unwrap_or(Path::new("."))
                .to_path_buf();
            session.handle.stop();
            self.screen = Screen::Browser(Browser::new(dir));
            self.needs_clear = true;
        }
    }

    fn tick(&mut self) {
        let now = Instant::now();
        self.toasts
            .retain(|(_, t)| now.duration_since(*t) < TOAST_TTL);
        let mut new_toasts: Vec<String> = Vec::new();
        if let Screen::Game(session) = &mut self.screen {
            for b in session.tracker.expire(now) {
                session.handle.send(EmuCommand::Button(b, false));
            }
            while let Ok(ev) = session.handle.events().try_recv() {
                match ev {
                    EmuEvent::Toast(m) => new_toasts.push(m),
                    EmuEvent::Error(m) => new_toasts.push(format!("Error: {m}")),
                }
            }
        }
        for t in new_toasts {
            self.toast(t);
        }
        // Copilot streaming.
        while let Some(msg) = self.copilot.poll() {
            match msg {
                CopilotMsg::Chunk(c) => {
                    self.thinking = true;
                    match self.panel_log.last_mut() {
                        Some((role, text)) if role == "ai*" => text.push_str(&c),
                        _ => self.panel_log.push(("ai*".into(), c)),
                    }
                }
                CopilotMsg::Done(full) => {
                    self.thinking = false;
                    if let Some((role, text)) = self.panel_log.last_mut()
                        && role == "ai*"
                    {
                        *role = "ai".into();
                        *text = full.clone();
                    } else {
                        self.panel_log.push(("ai".into(), full.clone()));
                    }
                    let question = self.pending_question.take().unwrap_or_default();
                    self.log_event(
                        Source::Copilot,
                        EventKind::Exchange {
                            question,
                            answer: full,
                            screenshot: None,
                        },
                    );
                }
                CopilotMsg::Error(e) => {
                    self.thinking = false;
                    self.panel_log.push(("err".into(), e));
                }
            }
        }
        // Autopilot events.
        let mut finished = false;
        if let UiMode::Autopilot { rx, .. } = &self.mode {
            while let Ok(ev) = rx.try_recv() {
                match ev {
                    PlannerEvent::Decided { action, outcome } => self
                        .panel_log
                        .push(("auto".into(), format!("{action} -> {outcome}"))),
                    PlannerEvent::Message(m) => self.panel_log.push(("auto".into(), m)),
                    PlannerEvent::Finished(r) => {
                        self.panel_log.push(("auto".into(), format!("done: {r}")));
                        finished = true;
                    }
                }
            }
        }
        if finished {
            self.mode = UiMode::Play;
        }
    }

    fn current_state_json(&self) -> serde_json::Value {
        if let Screen::Game(session) = &self.screen {
            let shared = session.handle.shared();
            let s = shared.lock().unwrap();
            if let Some(gs) = &s.game_state {
                return gs.to_json();
            }
        }
        serde_json::Value::Null
    }

    fn log_event(&self, source: Source, kind: EventKind) {
        let state = self.current_state_json();
        if let Ok(mut j) = self.journal.lock() {
            j.log(source, 0, state, kind);
        }
    }

    fn recent_journal_lines(&self) -> String {
        let path = self
            .journal
            .lock()
            .map(|j| j.dir().join("events.jsonl"))
            .unwrap_or_default();
        match std::fs::read_to_string(path) {
            Ok(text) => {
                let lines: Vec<&str> = text.lines().collect();
                let start = lines.len().saturating_sub(20);
                lines[start..].join("\n")
            }
            Err(_) => String::new(),
        }
    }

    fn submit_hint(&mut self, question: String) {
        let Screen::Game(session) = &self.screen else {
            return;
        };
        let shared = session.handle.shared();
        let (state_text, image_png) = {
            let s = shared.lock().unwrap();
            let text = s
                .game_state
                .as_ref()
                .map(|g| g.prompt_text())
                .unwrap_or_else(|| "state unavailable".into());
            let img = if self.cfg.vision && s.width > 0 {
                encode_png(&s.rgb, s.width as u32, s.height as u32)
            } else {
                None
            };
            (text, img)
        };
        let q = if question.trim().is_empty() {
            "What should I do here?".to_string()
        } else {
            question
        };
        self.panel_log.push(("you".into(), q.clone()));
        self.pending_question = Some(q.clone());
        self.thinking = true;
        self.copilot.ask_streaming(HintRequest {
            state_text,
            recent: self.recent_journal_lines(),
            question: q,
            image_png,
        });
    }

    fn start_autopilot(&mut self, goal: String) {
        let Screen::Game(session) = &self.screen else {
            return;
        };
        let goal = if goal.trim().is_empty() {
            "make progress toward the next objective".to_string()
        } else {
            goal
        };
        let abort = Arc::new(AtomicBool::new(false));
        let (tx, rx) = std::sync::mpsc::channel();
        let ctl = session.handle.controller();
        let cfg = self.cfg.clone();
        let journal = Arc::clone(&self.journal);
        let abort2 = Arc::clone(&abort);
        let goal2 = goal.clone();
        std::thread::spawn(move || {
            let driver = Driver::new(ctl, abort2, PathBuf::from("run/maps"));
            run_planner(
                |sys, user| ask_blocking(&cfg, sys, user),
                &driver,
                journal,
                goal2,
                tx,
            );
        });
        self.panel_log
            .push(("auto".into(), format!("goal: {goal}")));
        self.mode = UiMode::Autopilot { abort, rx };
    }

    fn handle_event(&mut self, ev: Event) {
        let Event::Key(key) = ev else { return };
        match &mut self.mode {
            UiMode::HintInput(buf) => {
                if key.kind != KeyEventKind::Press && key.kind != KeyEventKind::Repeat {
                    return;
                }
                match key.code {
                    KeyCode::Enter => {
                        let q = buf.clone();
                        self.mode = UiMode::Play;
                        self.submit_hint(q);
                    }
                    KeyCode::Esc => self.mode = UiMode::Play,
                    KeyCode::Backspace => {
                        buf.pop();
                    }
                    KeyCode::Char(c) => buf.push(c),
                    _ => {}
                }
                return;
            }
            UiMode::GoalInput(buf) => {
                if key.kind != KeyEventKind::Press && key.kind != KeyEventKind::Repeat {
                    return;
                }
                match key.code {
                    KeyCode::Enter => {
                        let g = buf.clone();
                        self.mode = UiMode::Play;
                        self.start_autopilot(g);
                    }
                    KeyCode::Esc => self.mode = UiMode::Play,
                    KeyCode::Backspace => {
                        buf.pop();
                    }
                    KeyCode::Char(c) => buf.push(c),
                    _ => {}
                }
                return;
            }
            UiMode::Autopilot { abort, .. } => {
                if key.kind == KeyEventKind::Press {
                    abort.store(true, Ordering::SeqCst);
                    if let Screen::Game(session) = &self.screen {
                        session.handle.send(EmuCommand::AbortOps);
                    }
                    self.panel_log
                        .push(("auto".into(), "aborting after current step…".into()));
                }
                return;
            }
            UiMode::Play => {}
        }
        match &self.screen {
            Screen::Browser(_) => {
                if key.kind == KeyEventKind::Press || key.kind == KeyEventKind::Repeat {
                    self.handle_browser_key(key.code);
                }
            }
            Screen::Game(_) => self.handle_game_key(key),
        }
    }

    fn handle_browser_key(&mut self, code: KeyCode) {
        let Screen::Browser(browser) = &mut self.screen else {
            return;
        };
        match code {
            KeyCode::Up => browser.up(),
            KeyCode::Down => browser.down(),
            KeyCode::Char('r') => browser.rescan(),
            KeyCode::Char('q') | KeyCode::Esc => self.quit = true,
            KeyCode::Enter => {
                if let Some(p) = browser.selected_path().map(Path::to_path_buf) {
                    self.start_game(&p);
                }
            }
            _ => {}
        }
    }

    fn handle_game_key(&mut self, key: ratatui::crossterm::event::KeyEvent) {
        // Copilot hotkeys first.
        if key.kind == KeyEventKind::Press {
            match key.code {
                KeyCode::Char('?') => {
                    if let Screen::Game(session) = &self.screen {
                        let paused = session.handle.shared().lock().unwrap().paused;
                        if !paused {
                            session.handle.send(EmuCommand::TogglePause);
                        }
                    }
                    self.mode = UiMode::HintInput(String::new());
                    return;
                }
                KeyCode::Tab => {
                    self.mode = UiMode::GoalInput(String::new());
                    return;
                }
                _ => {}
            }
        }
        let Screen::Game(session) = &mut self.screen else {
            return;
        };
        let now = Instant::now();
        // Kitty terminals deliver real Release events.
        if key.kind == KeyEventKind::Release {
            if let Some(b) = map_key(key.code)
                && session.tracker.release(b)
            {
                session.handle.send(EmuCommand::Button(b, false));
            }
            if key.code == KeyCode::Char(' ') {
                session.handle.send(EmuCommand::Turbo(false));
            }
            return;
        }
        match key.code {
            KeyCode::Char('q') | KeyCode::Char('Q') => self.quit = true,
            KeyCode::Esc => self.end_game(),
            KeyCode::Char('p') | KeyCode::Char('P') => session.handle.send(EmuCommand::TogglePause),
            KeyCode::Char('n') | KeyCode::Char('N') => session.handle.send(EmuCommand::FrameStep),
            KeyCode::Char(' ') if key.kind == KeyEventKind::Press => {
                if self.kitty {
                    // Hold-to-turbo: the Release event turns it off.
                    session.handle.send(EmuCommand::Turbo(true));
                } else {
                    // No Release events: Space toggles turbo.
                    let turbo = session.handle.shared().lock().unwrap().turbo;
                    session.handle.send(EmuCommand::Turbo(!turbo));
                }
            }
            KeyCode::F(n @ 1..=4) => {
                let slot = n;
                if key.modifiers.contains(KeyModifiers::SHIFT) {
                    session.handle.send(EmuCommand::LoadState(slot));
                } else {
                    session.handle.send(EmuCommand::SaveState(slot));
                }
            }
            code => {
                if let Some(b) = map_key(code)
                    && session.tracker.press(b, now)
                {
                    session.handle.send(EmuCommand::Button(b, true));
                    if now.duration_since(self.last_input_log) > Duration::from_millis(200) {
                        self.last_input_log = now;
                        let kind = EventKind::Input {
                            buttons: format!("{b:?}"),
                        };
                        self.log_event(Source::Human, kind);
                    }
                }
            }
        }
    }

    fn ai_status(&self) -> &'static str {
        match self.mode {
            UiMode::Autopilot { .. } => "autopilot",
            _ if self.thinking => "thinking",
            _ => "idle",
        }
    }

    fn draw(&mut self, f: &mut ratatui::Frame) {
        match &mut self.screen {
            Screen::Browser(browser) => {
                let block = Block::default()
                    .borders(Borders::ALL)
                    .title(format!(" gb-tui — {} ", browser.dir.display()));
                let items: Vec<ListItem> = if browser.entries.is_empty() {
                    vec![ListItem::new("No .gb/.gbc ROMs found — press r to rescan")]
                } else {
                    browser
                        .entries
                        .iter()
                        .map(|p| {
                            ListItem::new(
                                p.file_name()
                                    .unwrap_or_default()
                                    .to_string_lossy()
                                    .into_owned(),
                            )
                        })
                        .collect()
                };
                let list = List::new(items).block(block).highlight_style(
                    Style::default()
                        .add_modifier(Modifier::REVERSED)
                        .add_modifier(Modifier::BOLD),
                );
                let mut state = ListState::default();
                state.select(Some(browser.selected));
                f.render_stateful_widget(list, f.area(), &mut state);
            }
            Screen::Game(session) => {
                let area = f.area();
                let panel_wanted = !self.panel_log.is_empty()
                    || !matches!(self.mode, UiMode::Play)
                    || area.width > 120;
                let panel_w: u16 =
                    if !self.panel_log.is_empty() || !matches!(self.mode, UiMode::Play) {
                        36
                    } else {
                        26
                    };
                let (game_area, panel_area) = if panel_wanted && area.width > 60 {
                    let chunks =
                        Layout::horizontal([Constraint::Min(10), Constraint::Length(panel_w)])
                            .split(area);
                    (chunks[0], Some(chunks[1]))
                } else {
                    (area, None)
                };
                let shared = session.handle.shared();
                let s = shared.lock().unwrap();
                if s.width > 0 {
                    f.render_widget(
                        GameScreen {
                            rgb: &s.rgb,
                            width: s.width,
                            height: s.height,
                        },
                        game_area,
                    );
                }
                if let Some(panel) = panel_area {
                    let info = StatusInfo {
                        title: session.title.clone(),
                        cgb: session.cgb,
                        fps: s.fps,
                        paused: s.paused,
                        turbo: s.turbo,
                        muted: session.muted,
                    };
                    let mut lines: Vec<String> = status_lines(&info);
                    lines.push(format!("AI:    {}", self.ai_status()));
                    lines.push(String::new());
                    lines.extend(wrap_panel_lines(&self.panel_log, panel_w.saturating_sub(2)));
                    match &self.mode {
                        UiMode::HintInput(buf) => lines.push(format!("? {buf}_")),
                        UiMode::GoalInput(buf) => lines.push(format!("goal: {buf}_")),
                        UiMode::Autopilot { .. } => lines.push("[any key stops autopilot]".into()),
                        UiMode::Play => {}
                    }
                    let n_fit = panel.height.saturating_sub(2) as usize;
                    let start = lines.len().saturating_sub(n_fit);
                    let shown: Vec<Line> = lines[start..]
                        .iter()
                        .map(|l| Line::from(l.clone()))
                        .collect();
                    f.render_widget(
                        Paragraph::new(shown)
                            .block(Block::default().borders(Borders::ALL).title(" copilot ")),
                        panel,
                    );
                }
                drop(s);
                if let Some(hint) = zoom_hint(game_area.width, game_area.height) {
                    let w = (hint.chars().count() as u16).min(area.width);
                    let rect = Rect::new(area.x, area.y, w, 1);
                    f.render_widget(
                        Paragraph::new(hint)
                            .style(Style::default().fg(Color::Black).bg(Color::Yellow)),
                        rect,
                    );
                }
            }
        }
        // Toasts: bottom-left stack.
        let area = f.area();
        for (i, (msg, _)) in self.toasts.iter().rev().take(3).enumerate() {
            let y = area.height.saturating_sub(1 + i as u16);
            let w = (msg.chars().count() as u16).min(area.width);
            let rect = Rect::new(area.x, area.y + y, w, 1);
            f.render_widget(
                Paragraph::new(msg.as_str())
                    .style(Style::default().fg(Color::Black).bg(Color::Cyan)),
                rect,
            );
        }
    }
}

/// Encode RGB888 into an in-memory PNG (for multimodal hint requests).
fn encode_png(rgb: &[u8], w: u32, h: u32) -> Option<Vec<u8>> {
    if rgb.len() < (w * h * 3) as usize {
        return None;
    }
    let mut out = Vec::new();
    {
        let mut enc = png::Encoder::new(std::io::Cursor::new(&mut out), w, h);
        enc.set_color(png::ColorType::Rgb);
        enc.set_depth(png::BitDepth::Eight);
        enc.write_header().ok()?.write_image_data(rgb).ok()?;
    }
    Some(out)
}
