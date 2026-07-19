//! Journal → fine-tune dataset export.

use crate::copilot::SYSTEM_PROMPT;
use crate::gamestate::GameState;
use anyhow::{Context, Result};
use serde_json::{Value, json};
use std::path::Path;

fn read_events(journal_dir: &Path) -> Result<Vec<Value>> {
    let text = std::fs::read_to_string(journal_dir.join("events.jsonl"))
        .with_context(|| format!("no events.jsonl in {}", journal_dir.display()))?;
    Ok(text
        .lines()
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect())
}

fn state_text(ev: &Value) -> String {
    match serde_json::from_value::<GameState>(ev["state"].clone()) {
        Ok(gs) => gs.prompt_text(),
        Err(_) => String::new(),
    }
}

/// ShareGPT-style chat pairs from copilot exchanges.
pub fn export_advice(journal_dir: &Path, out: &mut impl std::io::Write) -> Result<usize> {
    let mut n = 0;
    for ev in read_events(journal_dir)? {
        if ev["event"] != "exchange" {
            continue;
        }
        let record = json!({
            "conversations": [
                {"from": "system", "value": SYSTEM_PROMPT},
                {"from": "human",
                 "value": format!("{}\n\n{}", state_text(&ev),
                                  ev["question"].as_str().unwrap_or(""))},
                {"from": "gpt", "value": ev["answer"].as_str().unwrap_or("")},
            ]
        });
        writeln!(out, "{record}")?;
        n += 1;
    }
    Ok(n)
}

/// Completion pairs (state → action) from autopilot decisions and human
/// input demonstrations.
pub fn export_policy(journal_dir: &Path, out: &mut impl std::io::Write) -> Result<usize> {
    let mut n = 0;
    for ev in read_events(journal_dir)? {
        let record = match ev["event"].as_str() {
            Some("decision") => json!({
                "prompt": format!("{}\nGOAL: {}", state_text(&ev),
                                  ev["goal"].as_str().unwrap_or("")),
                "completion": ev["action"].as_str().unwrap_or(""),
            }),
            Some("input") if ev["source"] == "human" => json!({
                "prompt": state_text(&ev),
                "completion": ev["buttons"].as_str().unwrap_or(""),
            }),
            _ => continue,
        };
        writeln!(out, "{record}")?;
        n += 1;
    }
    Ok(n)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fake_journal() -> tempfile::TempDir {
        let tmp = tempfile::tempdir().unwrap();
        let lines = [
            r#"{"ts":1,"frame":1,"source":"copilot","state":null,"event":"exchange","question":"help?","answer":"Use Ember.","screenshot":null}"#,
            r#"{"ts":2,"frame":2,"source":"autopilot","state":null,"event":"decision","goal":"win","action":"Fight","outcome":"Done","state_after":null}"#,
            r#"{"ts":3,"frame":3,"source":"human","state":null,"event":"input","buttons":"A"}"#,
        ];
        std::fs::write(tmp.path().join("events.jsonl"), lines.join("\n")).unwrap();
        tmp
    }

    #[test]
    fn exports_advice_pairs() {
        let tmp = fake_journal();
        let mut out = Vec::new();
        let n = export_advice(tmp.path(), &mut out).unwrap();
        assert_eq!(n, 1);
        let v: Value = serde_json::from_slice(&out).unwrap();
        assert_eq!(v["conversations"][2]["value"], "Use Ember.");
    }

    #[test]
    fn exports_policy_pairs() {
        let tmp = fake_journal();
        let mut out = Vec::new();
        let n = export_policy(tmp.path(), &mut out).unwrap();
        assert_eq!(n, 2);
        let lines: Vec<Value> = out
            .split(|b| *b == b'\n')
            .filter(|l| !l.is_empty())
            .map(|l| serde_json::from_slice(l).unwrap())
            .collect();
        assert_eq!(lines[0]["completion"], "Fight");
        assert_eq!(lines[1]["completion"], "A");
    }
}
