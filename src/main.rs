use anyhow::Result;
use gb_tui::ui::app::App;
use ratatui::crossterm::event::{
    KeyboardEnhancementFlags, PopKeyboardEnhancementFlags, PushKeyboardEnhancementFlags,
};
use ratatui::crossterm::execute;
use std::path::PathBuf;

/// The emulator core logs warnings to stderr; raw writes corrupt the TUI and
/// desync ratatui's buffer. Send them to a log file instead.
#[cfg(unix)]
fn redirect_stderr_to_log() {
    use std::os::fd::AsRawFd;
    let path = std::env::temp_dir().join("gb-tui.stderr.log");
    if let Ok(file) = std::fs::File::create(path) {
        unsafe {
            libc::dup2(file.as_raw_fd(), libc::STDERR_FILENO);
        }
        std::mem::forget(file);
    }
}

fn main() -> Result<()> {
    let start = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    if !start.exists() {
        anyhow::bail!("no such file or directory: {}", start.display());
    }
    #[cfg(unix)]
    redirect_stderr_to_log();

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
