//! LLM planner loop: the model picks one action per step from a fixed
//! vocabulary; deterministic macros execute it.

use super::{Driver, MacroResult};
use crate::journal::{EventKind, Journal, Source};
use anyhow::Result;
use std::sync::atomic::Ordering;
use std::sync::mpsc::Sender;
use std::sync::{Arc, Mutex};

#[derive(Debug, Clone, PartialEq)]
pub enum Action {
    Fight,
    Flee,
    UseItem(String),
    WalkTo(u8, u8),
    HealAtCenter,
    Interact,
    Press(String),
    Stop(String),
}

/// Extract an action from model output: first `{`..last `}` parsed as JSON.
pub fn parse_action(text: &str) -> Option<Action> {
    let start = text.find('{')?;
    let end = text.rfind('}')?;
    let v: serde_json::Value = serde_json::from_str(&text[start..=end]).ok()?;
    let action = v.get("action")?.as_str()?;
    Some(match action {
        "fight" => Action::Fight,
        "flee" => Action::Flee,
        "use_item" => Action::UseItem(v.get("item")?.as_str()?.to_string()),
        "walk_to" => Action::WalkTo(v.get("x")?.as_u64()? as u8, v.get("y")?.as_u64()? as u8),
        "heal_at_center" => Action::HealAtCenter,
        "interact" => Action::Interact,
        "press" => Action::Press(v.get("seq")?.as_str()?.to_string()),
        "stop" => Action::Stop(
            v.get("reason")
                .and_then(|r| r.as_str())
                .unwrap_or("stopped")
                .to_string(),
        ),
        _ => return None,
    })
}

pub const ACTION_VOCAB: &str = r#"Choose exactly ONE action and reply with ONLY a JSON object:
{"action":"fight"}                      - take one battle to completion with attacks
{"action":"flee"}                       - run from a wild battle
{"action":"use_item","item":"Potion"}   - use a bag item (partial name ok)
{"action":"walk_to","x":10,"y":5}       - walk to coordinates on the current map
{"action":"heal_at_center"}             - heal (only when inside a Pokemon Center)
{"action":"interact"}                   - press A at whatever is in front of you
{"action":"press","seq":"a:8 wait:60"}  - raw input script (buttons a,b,up,down,left,right; N=frames)
{"action":"stop","reason":"..."}        - hand control back to the player"#;

#[derive(Debug)]
pub enum PlannerEvent {
    Decided { action: String, outcome: String },
    Message(String),
    Finished(String),
}

const STEP_CAP: usize = 50;

/// Run the planning loop. `ask` is injected so tests can fake the model;
/// production passes a closure over `copilot::ask_blocking`.
pub fn run_planner(
    ask: impl Fn(&str, String) -> Result<String>,
    driver: &Driver,
    journal: Arc<Mutex<Journal>>,
    goal: String,
    events: Sender<PlannerEvent>,
) {
    let system = format!("{}\n\n{}", crate::copilot::SYSTEM_PROMPT, ACTION_VOCAB);
    for step in 0..STEP_CAP {
        if driver.abort.load(Ordering::SeqCst) {
            let _ = events.send(PlannerEvent::Finished("aborted by player".into()));
            return;
        }
        let gs = driver.state();
        let user = format!(
            "GOAL: {goal}\nSTEP: {step}\n\nCURRENT STATE:\n{}",
            gs.prompt_text()
        );
        let reply = match ask(&system, user.clone()) {
            Ok(r) => r,
            Err(e) => {
                let _ = events.send(PlannerEvent::Finished(format!("model error: {e}")));
                return;
            }
        };
        let action = match parse_action(&reply) {
            Some(a) => a,
            None => {
                let retry = format!(
                    "{user}\n\nYour previous reply could not be parsed. \
                     Reply with ONLY a JSON action object."
                );
                match ask(&system, retry).ok().and_then(|r| parse_action(&r)) {
                    Some(a) => a,
                    None => Action::Stop("model output unparseable".into()),
                }
            }
        };
        let action_desc = format!("{action:?}");
        let state_before = gs.to_json();
        let outcome = match &action {
            Action::Fight => driver.fight(),
            Action::Flee => driver.flee(),
            Action::UseItem(item) => driver.use_item(item),
            Action::WalkTo(x, y) => driver.walk_to(*x, *y),
            Action::HealAtCenter => driver.heal_at_center(),
            Action::Interact => driver.interact(),
            Action::Press(seq) => driver.press(seq),
            Action::Stop(reason) => {
                journal_decision(&journal, driver, &state_before, &goal, &action_desc, "stop");
                let _ = events.send(PlannerEvent::Finished(reason.clone()));
                return;
            }
        };
        let outcome_desc = format!("{outcome:?}");
        journal_decision(
            &journal,
            driver,
            &state_before,
            &goal,
            &action_desc,
            &outcome_desc,
        );
        let _ = events.send(PlannerEvent::Decided {
            action: action_desc,
            outcome: outcome_desc,
        });
        if outcome == MacroResult::Aborted {
            let _ = events.send(PlannerEvent::Finished("aborted by player".into()));
            return;
        }
    }
    let _ = events.send(PlannerEvent::Finished("step cap reached".into()));
}

fn journal_decision(
    journal: &Arc<Mutex<Journal>>,
    driver: &Driver,
    state_before: &serde_json::Value,
    goal: &str,
    action: &str,
    outcome: &str,
) {
    let after = driver.state();
    if let Ok(mut j) = journal.lock() {
        j.log(
            Source::Autopilot,
            0,
            state_before.clone(),
            EventKind::Decision {
                goal: goal.to_string(),
                action: action.to_string(),
                outcome: outcome.to_string(),
                state_after: after.to_json(),
            },
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_actions_from_noisy_text() {
        assert_eq!(
            parse_action(r#"I think {"action":"fight"} is best"#),
            Some(Action::Fight)
        );
        assert_eq!(
            parse_action(r#"{"action":"walk_to","x":10,"y":5}"#),
            Some(Action::WalkTo(10, 5))
        );
        assert_eq!(
            parse_action(r#"{"action":"use_item","item":"Potion"}"#),
            Some(Action::UseItem("Potion".into()))
        );
        assert_eq!(
            parse_action(r#"{"action":"stop"}"#),
            Some(Action::Stop("stopped".into()))
        );
        assert_eq!(parse_action("no json here"), None);
        assert_eq!(parse_action(r#"{"action":"dance"}"#), None);
    }

    #[test]
    fn planner_stops_on_stop_action_and_journals() {
        use crate::core::EmulatorCore;
        use crate::core::gb::GbCore;
        use crate::emu::{EmuConfig, EmuHandle};
        use std::sync::atomic::AtomicBool;

        let rom = std::path::Path::new("test-roms/pokemon-red.gb");
        let state = std::path::Path::new("run/ck-BOULDER-BADGE.state");
        if !rom.exists() || !state.exists() {
            eprintln!("SKIP: fixtures absent");
            return;
        }
        let mut core = GbCore::new();
        core.load_rom(&std::fs::read(rom).unwrap(), None).unwrap();
        core.load_state(&std::fs::read(state).unwrap()).unwrap();
        let handle = EmuHandle::spawn(
            Box::new(core),
            EmuConfig {
                frame_duration: std::time::Duration::from_micros(200),
                ring: None,
                rom_path: None,
            },
        );
        let driver = Driver::new(
            handle.controller(),
            Arc::new(AtomicBool::new(false)),
            std::path::PathBuf::from("run/maps"),
        );
        let tmp = tempfile::tempdir().unwrap();
        let journal = Arc::new(Mutex::new(Journal::create(tmp.path()).unwrap()));
        let (tx, rx) = std::sync::mpsc::channel();
        run_planner(
            |_sys, _user| Ok(r#"{"action":"stop","reason":"test done"}"#.into()),
            &driver,
            Arc::clone(&journal),
            "test goal".into(),
            tx,
        );
        let mut finished = None;
        while let Ok(ev) = rx.try_recv() {
            if let PlannerEvent::Finished(r) = ev {
                finished = Some(r);
            }
        }
        assert_eq!(finished.as_deref(), Some("test done"));
        let dir = journal.lock().unwrap().dir().to_path_buf();
        let text = std::fs::read_to_string(dir.join("events.jsonl")).unwrap();
        assert!(text.contains(r#""event":"decision""#));
        handle.stop();
    }
}
