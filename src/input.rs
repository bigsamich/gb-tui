use crate::core::Button;
use ratatui::crossterm::event::KeyCode;
use std::collections::HashMap;
use std::time::{Duration, Instant};

const AUTO_RELEASE: Duration = Duration::from_millis(150);

pub fn map_key(code: KeyCode) -> Option<Button> {
    match code {
        KeyCode::Up => Some(Button::Up),
        KeyCode::Down => Some(Button::Down),
        KeyCode::Left => Some(Button::Left),
        KeyCode::Right => Some(Button::Right),
        KeyCode::Char('z') | KeyCode::Char('Z') => Some(Button::B),
        KeyCode::Char('x') | KeyCode::Char('X') => Some(Button::A),
        KeyCode::Enter => Some(Button::Start),
        KeyCode::Backspace => Some(Button::Select),
        _ => None,
    }
}

pub struct KeyTracker {
    kitty: bool,
    held: HashMap<Button, Instant>,
}

impl KeyTracker {
    pub fn new(kitty: bool) -> Self {
        Self {
            kitty,
            held: HashMap::new(),
        }
    }

    /// Returns true if the button was newly pressed (not a repeat refresh).
    pub fn press(&mut self, b: Button, now: Instant) -> bool {
        self.held.insert(b, now).is_none()
    }

    /// Kitty path: returns true if the button was held.
    pub fn release(&mut self, b: Button) -> bool {
        self.held.remove(&b).is_some()
    }

    /// Non-kitty: buttons not refreshed by OS auto-repeat within the window
    /// are released. Kitty terminals get real Release events instead.
    pub fn expire(&mut self, now: Instant) -> Vec<Button> {
        if self.kitty {
            return Vec::new();
        }
        let expired: Vec<Button> = self
            .held
            .iter()
            .filter(|(_, t)| now.duration_since(**t) >= AUTO_RELEASE)
            .map(|(b, _)| *b)
            .collect();
        for b in &expired {
            self.held.remove(b);
        }
        expired
    }

    pub fn release_all(&mut self) -> Vec<Button> {
        self.held.drain().map(|(b, _)| b).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::Button;
    use ratatui::crossterm::event::KeyCode;
    use std::time::{Duration, Instant};

    #[test]
    fn keymap_defaults() {
        assert_eq!(map_key(KeyCode::Up), Some(Button::Up));
        assert_eq!(map_key(KeyCode::Char('z')), Some(Button::B));
        assert_eq!(map_key(KeyCode::Char('Z')), Some(Button::B));
        assert_eq!(map_key(KeyCode::Char('x')), Some(Button::A));
        assert_eq!(map_key(KeyCode::Enter), Some(Button::Start));
        assert_eq!(map_key(KeyCode::Backspace), Some(Button::Select));
        assert_eq!(map_key(KeyCode::Char('q')), None);
    }

    #[test]
    fn non_kitty_auto_release_after_timeout() {
        let mut t = KeyTracker::new(false);
        let t0 = Instant::now();
        assert!(t.press(Button::Right, t0));
        assert!(!t.press(Button::Right, t0 + Duration::from_millis(50))); // repeat refresh
        // 100ms after refresh: still held
        assert!(t.expire(t0 + Duration::from_millis(140)).is_empty());
        // 150ms+ after last refresh: released
        assert_eq!(
            t.expire(t0 + Duration::from_millis(201)),
            vec![Button::Right]
        );
        assert!(t.expire(t0 + Duration::from_millis(300)).is_empty()); // only once
    }

    #[test]
    fn kitty_mode_never_auto_releases() {
        let mut t = KeyTracker::new(true);
        let t0 = Instant::now();
        t.press(Button::A, t0);
        assert!(t.expire(t0 + Duration::from_secs(5)).is_empty());
        assert!(t.release(Button::A));
        assert!(!t.release(Button::A)); // already released
    }

    #[test]
    fn release_all_returns_held() {
        let mut t = KeyTracker::new(false);
        let t0 = Instant::now();
        t.press(Button::A, t0);
        t.press(Button::Up, t0);
        let mut all = t.release_all();
        all.sort_by_key(|b| format!("{b:?}"));
        assert_eq!(all, vec![Button::A, Button::Up]);
        assert!(t.release_all().is_empty());
    }
}
