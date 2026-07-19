use super::screen::{IDEAL_COLS, IDEAL_ROWS};

pub struct StatusInfo {
    pub title: String,
    pub cgb: bool,
    pub fps: f32,
    pub paused: bool,
    pub turbo: bool,
    pub muted: bool,
}

pub fn status_lines(info: &StatusInfo) -> Vec<String> {
    let speed = if info.paused {
        "Paused"
    } else if info.turbo {
        "Turbo"
    } else {
        "1x"
    };
    let audio = if info.muted {
        "Muted (no device)"
    } else {
        "44.1kHz"
    };
    let mode = if info.cgb { "CGB" } else { "DMG" };
    vec![
        info.title.clone(),
        format!("Mode:  {mode}"),
        format!("FPS:   {:.1}", info.fps),
        format!("Speed: {speed}"),
        format!("Audio: {audio}"),
        String::new(),
        "Arrows:D-pad  Z:B X:A".into(),
        "Enter:Start Bksp:Select".into(),
        "Space:Turbo P:Pause".into(),
        "N:Step F1-4:Save".into(),
        "Shift+F1-4:Load".into(),
        "Esc:Browser Q:Quit".into(),
    ]
}

pub fn zoom_hint(cols: u16, rows: u16) -> Option<String> {
    if cols >= IDEAL_COLS && rows >= IDEAL_ROWS {
        return None;
    }
    Some(format!(
        "Zoom out (Ctrl+-) for full res — {cols}x{rows} of {IDEAL_COLS}x{IDEAL_ROWS}"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn info() -> StatusInfo {
        StatusInfo {
            title: "POKEMON RED".into(),
            cgb: false,
            fps: 59.7,
            paused: false,
            turbo: false,
            muted: false,
        }
    }

    #[test]
    fn status_lines_content() {
        let lines = status_lines(&info());
        let joined = lines.join("\n");
        assert!(joined.contains("POKEMON RED"));
        assert!(joined.contains("DMG"));
        assert!(joined.contains("59.7"));
        assert!(joined.contains("1x"));
        assert!(joined.contains("44.1kHz"));
        assert!(joined.contains("Z:B"), "keymap missing: {joined}");
    }

    #[test]
    fn status_lines_variants() {
        let mut i = info();
        i.paused = true;
        i.muted = true;
        i.cgb = true;
        let joined = status_lines(&i).join("\n");
        assert!(joined.contains("Paused"));
        assert!(joined.contains("Muted"));
        assert!(joined.contains("CGB"));
        let mut i = info();
        i.turbo = true;
        assert!(status_lines(&i).join("\n").contains("Turbo"));
    }

    #[test]
    fn zoom_hint_only_when_small() {
        assert!(zoom_hint(160, 72).is_none());
        assert!(zoom_hint(200, 80).is_none());
        let hint = zoom_hint(112, 50).unwrap();
        assert!(hint.contains("112x50"));
        assert!(hint.contains("160x72"));
        assert!(hint.contains("Ctrl+-"));
    }
}
