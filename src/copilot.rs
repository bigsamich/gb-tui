//! Local-model copilot: Ollama HTTP client, config, and the system prompt.

use anyhow::{Context, Result, anyhow};
use serde_json::json;
use std::io::BufRead;
use std::path::PathBuf;
use std::sync::mpsc::{Receiver, Sender, channel};

pub const SYSTEM_PROMPT: &str = "You are an expert Pokemon Red assistant watching a live \
game. Answer concisely for a player mid-session: give the concrete next action first, \
then at most a few sentences of why. Hard-won knowledge you should apply:\n\
- Gen 1 has a single Special stat. Geodude and Onix have Special 30, so special attacks \
like Ember beat them even through the rock resistance; physical attacks bounce off their \
high Defense.\n\
- Growl stacking (-6 Attack) reduces enemy physical damage to 1-2 and trivializes long \
fights; it does not feed Bide.\n\
- Poison ticks 1 HP per 4 steps in the overworld and CAN black you out; carry Antidotes \
in Viridian Forest (Weedle's Poison Sting).\n\
- Ember is 2x vs Bug types. Electric does nothing to Ground (Onix, Geodude, Diglett).\n\
- Pokemon Centers restore PP as well as HP. Blacking out costs half your money.\n\
- Ledges are one-way southward. DSum step patterns can force rare encounters like \
Pikachu in Viridian Forest.\n\
- In menus, the move cursor position persists and drifts; misselected Growl casts are a \
common cause of 'nothing is happening' fights.";

#[derive(Clone, Debug)]
pub struct Config {
    pub ollama_url: String,
    pub model: String,
    pub vision: bool,
    pub journal_dir: PathBuf,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            ollama_url: "http://localhost:11434".into(),
            model: "qwen2.5:14b".into(),
            vision: false,
            journal_dir: PathBuf::from("journal"),
        }
    }
}

impl Config {
    /// Loads `gb-tui.toml` from the working directory; missing file or keys
    /// fall back to defaults.
    pub fn load() -> Config {
        let mut cfg = Config::default();
        let Ok(text) = std::fs::read_to_string("gb-tui.toml") else {
            return cfg;
        };
        let Ok(value) = text.parse::<toml::Table>() else {
            eprintln!("gb-tui.toml: parse error, using defaults");
            return cfg;
        };
        if let Some(s) = value.get("ollama_url").and_then(|v| v.as_str()) {
            cfg.ollama_url = s.to_string();
        }
        if let Some(s) = value.get("model").and_then(|v| v.as_str()) {
            cfg.model = s.to_string();
        }
        if let Some(b) = value.get("vision").and_then(|v| v.as_bool()) {
            cfg.vision = b;
        }
        if let Some(s) = value.get("journal_dir").and_then(|v| v.as_str()) {
            cfg.journal_dir = PathBuf::from(s);
        }
        cfg
    }
}

pub struct HintRequest {
    pub state_text: String,
    pub recent: String,
    pub question: String,
    pub image_png: Option<Vec<u8>>,
}

pub enum CopilotMsg {
    Chunk(String),
    Done(String),
    Error(String),
}

/// Background copilot worker. Requests go in; streamed chunks come out.
pub struct Copilot {
    req_tx: Sender<HintRequest>,
    resp_rx: Receiver<CopilotMsg>,
}

impl Copilot {
    pub fn spawn(cfg: Config) -> Copilot {
        let (req_tx, req_rx) = channel::<HintRequest>();
        let (resp_tx, resp_rx) = channel::<CopilotMsg>();
        std::thread::spawn(move || {
            while let Ok(req) = req_rx.recv() {
                if let Err(e) = stream_chat(&cfg, &req, &resp_tx) {
                    let _ = resp_tx.send(CopilotMsg::Error(format!(
                        "{e:#} — is Ollama running? try: ollama serve"
                    )));
                }
            }
        });
        Copilot { req_tx, resp_rx }
    }

    pub fn ask_streaming(&self, req: HintRequest) {
        let _ = self.req_tx.send(req);
    }

    pub fn poll(&self) -> Option<CopilotMsg> {
        self.resp_rx.try_recv().ok()
    }
}

fn user_content(req: &HintRequest) -> String {
    let mut s = String::new();
    s.push_str("CURRENT GAME STATE:\n");
    s.push_str(&req.state_text);
    if !req.recent.is_empty() {
        s.push_str("\nRECENT EVENTS:\n");
        s.push_str(&req.recent);
    }
    s.push_str("\nQUESTION: ");
    s.push_str(&req.question);
    s
}

fn stream_chat(cfg: &Config, req: &HintRequest, out: &Sender<CopilotMsg>) -> Result<()> {
    let mut user = json!({"role": "user", "content": user_content(req)});
    if let Some(png) = &req.image_png {
        user["images"] = json!([base64(png)]);
    }
    let body = json!({
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            user,
        ],
        "stream": true,
    });
    let resp = ureq::post(&format!("{}/api/chat", cfg.ollama_url))
        .timeout(std::time::Duration::from_secs(120))
        .send_json(body)
        .context("Ollama request failed")?;
    let reader = std::io::BufReader::new(resp.into_reader());
    let mut full = String::new();
    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let v: serde_json::Value = serde_json::from_str(&line)?;
        if let Some(chunk) = v["message"]["content"].as_str()
            && !chunk.is_empty()
        {
            full.push_str(chunk);
            let _ = out.send(CopilotMsg::Chunk(chunk.to_string()));
        }
        if v["done"].as_bool() == Some(true) {
            break;
        }
    }
    let _ = out.send(CopilotMsg::Done(full));
    Ok(())
}

/// Non-streaming request used by the autopilot planner.
pub fn ask_blocking(cfg: &Config, system: &str, user: String) -> Result<String> {
    ask_blocking_img(cfg, system, user, None)
}

/// Non-streaming request with an optional attached image (vision models).
pub fn ask_blocking_img(
    cfg: &Config,
    system: &str,
    user: String,
    image_png: Option<Vec<u8>>,
) -> Result<String> {
    ask_blocking_inner(cfg, system, user, image_png, false)
}

/// Non-streaming request with Ollama's enforced-JSON output mode — the
/// planner uses this so action replies are always parseable.
pub fn ask_blocking_json(
    cfg: &Config,
    system: &str,
    user: String,
    image_png: Option<Vec<u8>>,
) -> Result<String> {
    ask_blocking_inner(cfg, system, user, image_png, true)
}

fn ask_blocking_inner(
    cfg: &Config,
    system: &str,
    user: String,
    image_png: Option<Vec<u8>>,
    json_mode: bool,
) -> Result<String> {
    let mut user_msg = json!({"role": "user", "content": user});
    if let Some(png) = image_png {
        user_msg["images"] = json!([base64(&png)]);
    }
    let mut body = json!({
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": system},
            user_msg,
        ],
        "stream": false,
    });
    if json_mode {
        body["format"] = json!("json");
    }
    let resp = ureq::post(&format!("{}/api/chat", cfg.ollama_url))
        .timeout(std::time::Duration::from_secs(120))
        .send_json(body)
        .context("Ollama request failed")?;
    let v: serde_json::Value = resp.into_json()?;
    v["message"]["content"]
        .as_str()
        .map(|s| s.to_string())
        .ok_or_else(|| anyhow!("malformed Ollama response"))
}

/// Standard base64 (no external dependency).
pub fn base64(data: &[u8]) -> String {
    const ALPHA: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b = [
            chunk[0],
            chunk.get(1).copied().unwrap_or(0),
            chunk.get(2).copied().unwrap_or(0),
        ];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        out.push(ALPHA[(n >> 18) as usize & 63] as char);
        out.push(ALPHA[(n >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 {
            ALPHA[(n >> 6) as usize & 63] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            ALPHA[n as usize & 63] as char
        } else {
            '='
        });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;

    fn mock_ollama(body_lines: Vec<String>) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            if let Ok((mut sock, _)) = listener.accept() {
                let mut buf = [0u8; 8192];
                let _ = sock.read(&mut buf);
                let body = body_lines.join("\n");
                let resp = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nContent-Length: {}\r\n\r\n{}",
                    body.len(),
                    body
                );
                let _ = sock.write_all(resp.as_bytes());
            }
        });
        format!("http://{addr}")
    }

    #[test]
    fn streams_chat_chunks() {
        let url = mock_ollama(vec![
            r#"{"message":{"content":"Use "},"done":false}"#.into(),
            r#"{"message":{"content":"Ember"},"done":false}"#.into(),
            r#"{"message":{"content":""},"done":true}"#.into(),
        ]);
        let cfg = Config {
            ollama_url: url,
            ..Config::default()
        };
        let copilot = Copilot::spawn(cfg);
        copilot.ask_streaming(HintRequest {
            state_text: "state".into(),
            recent: String::new(),
            question: "what now?".into(),
            image_png: None,
        });
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let mut full = None;
        while std::time::Instant::now() < deadline {
            match copilot.poll() {
                Some(CopilotMsg::Done(f)) => {
                    full = Some(f);
                    break;
                }
                Some(CopilotMsg::Error(e)) => panic!("copilot error: {e}"),
                _ => std::thread::sleep(std::time::Duration::from_millis(5)),
            }
        }
        assert_eq!(full.as_deref(), Some("Use Ember"));
    }

    #[test]
    fn config_defaults_without_file() {
        let cfg = Config::default();
        assert_eq!(cfg.ollama_url, "http://localhost:11434");
        assert!(!cfg.vision);
    }

    #[test]
    fn base64_encodes() {
        assert_eq!(base64(b"hi"), "aGk=");
        assert_eq!(base64(b"hey"), "aGV5");
        assert_eq!(base64(b""), "");
    }
}

#[cfg(test)]
mod live_tests {
    use super::*;

    #[test]
    #[ignore = "needs live Ollama"]
    fn live_ask_blocking() {
        let cfg = Config {
            model: "gpt-oss:20b".into(),
            ..Config::default()
        };
        match ask_blocking(&cfg, "You are terse.", "say hi in 3 words".into()) {
            Ok(r) => println!("LIVE OK: {r}"),
            Err(e) => panic!("LIVE ERR: {e:#}"),
        }
    }
}

#[cfg(test)]
mod live_stream_tests {
    use super::*;

    #[test]
    #[ignore = "needs live Ollama"]
    fn live_streaming() {
        let cfg = Config {
            model: "gpt-oss:20b".into(),
            ..Config::default()
        };
        let c = Copilot::spawn(cfg);
        c.ask_streaming(HintRequest {
            state_text: "Location: Pewter Gym".into(),
            recent: String::new(),
            question: "say ok".into(),
            image_png: None,
        });
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(120);
        loop {
            match c.poll() {
                Some(CopilotMsg::Done(f)) => {
                    println!("STREAM OK: {}", &f[..f.len().min(60)]);
                    break;
                }
                Some(CopilotMsg::Error(e)) => panic!("STREAM ERR: {e}"),
                _ => std::thread::sleep(std::time::Duration::from_millis(20)),
            }
            assert!(std::time::Instant::now() < deadline, "timeout");
        }
    }
}
