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

/// Wrap copilot chat entries into panel-width lines with role prefixes.
pub fn wrap_panel_lines(log: &[(String, String)], width: u16) -> Vec<String> {
    let width = width.max(8) as usize;
    let mut out = Vec::new();
    for (role, text) in log {
        let prefix = match role.as_str() {
            "you" => "you> ",
            "ai" | "ai*" => "ai>  ",
            "auto" => "auto ",
            "err" => "err  ",
            _ => "     ",
        };
        let mut line = String::from(prefix);
        for word in text.split_whitespace() {
            if line.len() + word.len() + 1 > width && line.len() > prefix.len() {
                out.push(line.clone());
                line = String::from("     ");
            }
            if line.len() > 5 || !line.trim().is_empty() {
                line.push(' ');
            }
            line.push_str(word);
        }
        if !line.trim().is_empty() {
            out.push(line);
        }
    }
    out
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

    #[test]
    fn wraps_panel_lines_with_prefixes() {
        let log = vec![
            ("you".to_string(), "how do I beat Brock quickly".to_string()),
            ("ai".to_string(), "Use Ember".to_string()),
        ];
        let lines = wrap_panel_lines(&log, 16);
        assert!(lines[0].starts_with("you> "));
        assert!(lines.iter().any(|l| l.contains("Ember")));
        assert!(lines.iter().all(|l| l.len() <= 20));
    }
}
