use crate::audio::AudioOutput;
use crate::core::EmulatorCore;
use crate::core::gb::GbCore;
use crate::emu::{EmuCommand, EmuConfig, EmuEvent, EmuHandle};
use crate::input::{KeyTracker, map_key};
use crate::persist;
use crate::ui::browser::Browser;
use crate::ui::screen::GameScreen;
use crate::ui::status::{StatusInfo, status_lines, zoom_hint};
use anyhow::Result;
use ratatui::DefaultTerminal;
use ratatui::crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::Line;
use ratatui::widgets::{Block, Borders, List, ListItem, ListState, Paragraph};
use std::path::{Path, PathBuf};
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

pub struct App {
    screen: Screen,
    audio: Option<AudioOutput>,
    kitty: bool,
    toasts: Vec<(String, Instant)>,
    quit: bool,
}

impl App {
    pub fn run(terminal: &mut DefaultTerminal, start: PathBuf, kitty: bool) -> Result<()> {
        let audio = AudioOutput::try_new();
        let browser_dir = if start.is_file() {
            start.parent().unwrap_or(Path::new(".")).to_path_buf()
        } else {
            start.clone()
        };
        let mut app = App {
            screen: Screen::Browser(Browser::new(browser_dir)),
            audio,
            kitty,
            toasts: Vec::new(),
            quit: false,
        };
        if start.is_file() {
            app.start_game(&start);
        }
        while !app.quit {
            app.tick();
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
    }

    fn handle_event(&mut self, ev: Event) {
        let Event::Key(key) = ev else { return };
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
                }
            }
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
                // Reserve a 26-col status panel when it fits.
                let (game_area, panel_area) = if area.width > 120 {
                    let chunks = Layout::horizontal([Constraint::Min(10), Constraint::Length(26)])
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
                    let lines: Vec<Line> =
                        status_lines(&info).into_iter().map(Line::from).collect();
                    f.render_widget(
                        Paragraph::new(lines)
                            .block(Block::default().borders(Borders::ALL).title(" status ")),
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
