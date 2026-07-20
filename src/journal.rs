//! Append-only JSONL gameplay journal shared by gb-tui and gb-agent.

use anyhow::Result;
use serde::Serialize;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    Human,
    Copilot,
    Autopilot,
    Agent,
}

#[derive(Serialize)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum EventKind {
    Input {
        buttons: String,
    },
    Exchange {
        question: String,
        answer: String,
        screenshot: Option<String>,
    },
    Decision {
        goal: String,
        action: String,
        outcome: String,
        state_after: serde_json::Value,
    },
    Note {
        text: String,
    },
}

#[derive(Serialize)]
struct Envelope {
    ts: u64,
    frame: u64,
    source: Source,
    state: serde_json::Value,
    #[serde(flatten)]
    kind: EventKind,
}

pub struct Journal {
    dir: PathBuf,
    file: File,
    shot_idx: u32,
}

impl Journal {
    pub fn create(base: &Path) -> Result<Journal> {
        let stamp = {
            let secs = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
            // Simple sortable stamp without a chrono dependency.
            format!("session-{secs}")
        };
        let dir = base.join(stamp);
        fs::create_dir_all(&dir)?;
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.join("events.jsonl"))?;
        Ok(Journal {
            dir,
            file,
            shot_idx: 0,
        })
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    /// Append one event. Failures are reported to stderr, never propagated.
    pub fn log(&mut self, source: Source, frame: u64, state: serde_json::Value, kind: EventKind) {
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let env = Envelope {
            ts,
            frame,
            source,
            state,
            kind,
        };
        match serde_json::to_string(&env) {
            Ok(line) => {
                if let Err(e) = writeln!(self.file, "{line}").and_then(|_| self.file.flush()) {
                    eprintln!("journal write failed: {e}");
                }
            }
            Err(e) => eprintln!("journal serialize failed: {e}"),
        }
    }

    /// Save an RGB888 framebuffer as a numbered PNG; returns the file name.
    pub fn save_screenshot(&mut self, rgb: &[u8], w: u32, h: u32) -> Option<String> {
        if rgb.len() < (w * h * 3) as usize {
            return None;
        }
        self.shot_idx += 1;
        let name = format!("{:05}.png", self.shot_idx);
        let path = self.dir.join(&name);
        let write = || -> Result<()> {
            let file = File::create(&path)?;
            let mut enc = png::Encoder::new(std::io::BufWriter::new(file), w, h);
            enc.set_color(png::ColorType::Rgb);
            enc.set_depth(png::BitDepth::Eight);
            enc.write_header()?.write_image_data(rgb)?;
            Ok(())
        };
        match write() {
            Ok(()) => Some(name),
            Err(e) => {
                eprintln!("screenshot write failed: {e}");
                None
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn logs_events_as_jsonl() {
        let tmp = tempfile::tempdir().unwrap();
        let mut j = Journal::create(tmp.path()).unwrap();
        j.log(
            Source::Human,
            10,
            serde_json::json!({"map": 54}),
            EventKind::Input {
                buttons: "a:8".into(),
            },
        );
        j.log(
            Source::Copilot,
            20,
            serde_json::json!({"map": 54}),
            EventKind::Exchange {
                question: "help?".into(),
                answer: "Use Ember.".into(),
                screenshot: None,
            },
        );
        let text = std::fs::read_to_string(j.dir().join("events.jsonl")).unwrap();
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines.len(), 2);
        let first: serde_json::Value = serde_json::from_str(lines[0]).unwrap();
        assert_eq!(first["event"], "input");
        assert_eq!(first["buttons"], "a:8");
        assert_eq!(first["source"], "human");
        assert_eq!(first["state"]["map"], 54);
        let second: serde_json::Value = serde_json::from_str(lines[1]).unwrap();
        assert_eq!(second["event"], "exchange");
        assert_eq!(second["answer"], "Use Ember.");
    }

    #[test]
    fn saves_decodable_screenshot() {
        let tmp = tempfile::tempdir().unwrap();
        let mut j = Journal::create(tmp.path()).unwrap();
        let rgb = [255u8, 0, 0, 0, 255, 0, 0, 0, 255, 9, 9, 9];
        let name = j.save_screenshot(&rgb, 2, 2).unwrap();
        let bytes = std::fs::read(j.dir().join(name)).unwrap();
        assert_eq!(&bytes[1..4], b"PNG");
    }
}
