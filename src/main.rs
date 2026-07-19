use anyhow::Result;
use gb_tui::ui::app::App;
use ratatui::crossterm::event::{
    KeyboardEnhancementFlags, PopKeyboardEnhancementFlags, PushKeyboardEnhancementFlags,
};
use ratatui::crossterm::execute;
use std::path::PathBuf;

fn main() -> Result<()> {
    let start = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    if !start.exists() {
        anyhow::bail!("no such file or directory: {}", start.display());
    }

    let kitty = ratatui::crossterm::terminal::supports_keyboard_enhancement().unwrap_or(false);
    let mut terminal = ratatui::init(); // raw mode + alt screen + panic hook
    if kitty {
        let _ = execute!(
            std::io::stdout(),
            PushKeyboardEnhancementFlags(KeyboardEnhancementFlags::REPORT_EVENT_TYPES)
        );
    }

    let result = App::run(&mut terminal, start, kitty);

    if kitty {
        let _ = execute!(std::io::stdout(), PopKeyboardEnhancementFlags);
    }
    ratatui::restore();
    result
}
