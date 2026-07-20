//! gb-agent: headless play harness for AI-driven gameplay.
//!
//! Each invocation is one atomic action:
//!   load state (or boot fresh) -> run input script -> save state -> screenshot + peeks
//!
//! Usage:
//!   gb-agent --rom <rom> --state <file> [--new] [--script "<tokens>"]
//!            [--shot <png>] [--peek] [--peekhex <ADDR>:<LEN>]
//!
//! Script tokens (space-separated, executed in order):
//!   up:N down:N left:N right:N a:N b:N start:N select:N   hold button for N frames
//!   wait:N                                                run N frames with no input
//!   mash-a:N                                              N cycles of (A for 2, idle 16)
//!                                                         — advances dialogue quickly
//! One overworld walking step = 16 frames of a held direction.

use anyhow::{Context, Result, anyhow, bail};
use gb_tui::core::gb::GbCore;
use gb_tui::core::{Button, EmulatorCore};
use gb_tui::emu::{Op, parse_script};
use std::path::PathBuf;

const FRAME_W: usize = 160;
const FRAME_H: usize = 144;
const SCALE: usize = 3;

fn run_frames(core: &mut GbCore, n: u32) {
    for _ in 0..n {
        core.run_frame();
    }
}

fn exec(core: &mut GbCore, ops: &[Op]) -> u64 {
    let mut frames: u64 = 0;
    for op in ops {
        match op {
            Op::Hold(b, n) => {
                core.set_button(*b, true);
                run_frames(core, *n);
                core.set_button(*b, false);
                // settle frames so consecutive holds register separately
                run_frames(core, 2);
                frames += *n as u64 + 2;
            }
            Op::Wait(n) => {
                run_frames(core, *n);
                frames += *n as u64;
            }
            Op::MashA(n) => {
                for _ in 0..*n {
                    core.set_button(Button::A, true);
                    run_frames(core, 2);
                    core.set_button(Button::A, false);
                    run_frames(core, 16);
                }
                frames += *n as u64 * 18;
            }
        }
    }
    frames
}

fn write_png(path: &PathBuf, rgb: &[u8]) -> Result<()> {
    let (w, h) = (FRAME_W * SCALE, FRAME_H * SCALE);
    let mut out = vec![0u8; w * h * 3];
    for y in 0..h {
        for x in 0..w {
            let si = ((y / SCALE) * FRAME_W + (x / SCALE)) * 3;
            let di = (y * w + x) * 3;
            out[di..di + 3].copy_from_slice(&rgb[si..si + 3]);
        }
    }
    let file = std::fs::File::create(path)?;
    let mut enc = png::Encoder::new(std::io::BufWriter::new(file), w as u32, h as u32);
    enc.set_color(png::ColorType::Rgb);
    enc.set_depth(png::BitDepth::Eight);
    enc.write_header()?.write_image_data(&out)?;
    Ok(())
}

/// Pokemon Red (US) RAM locations of interest.
fn print_peeks(core: &GbCore) {
    let map = core.peek(0xD35E);
    let x = core.peek(0xD362);
    let y = core.peek(0xD361);
    let badges = core.peek(0xD356);
    let party = core.peek(0xD163);
    let mon1_level = core.peek(0xD18C);
    let in_battle = core.peek(0xD057);
    let mon1_hp = ((core.peek(0xD16C) as u16) << 8) | core.peek(0xD16D) as u16;
    let mon1_max = ((core.peek(0xD18D) as u16) << 8) | core.peek(0xD18E) as u16;
    println!(
        "peek: map={map} x={x} y={y} badges={badges:#04x} party={party} \
         mon1(level={mon1_level} hp={mon1_hp}/{mon1_max}) in_battle={in_battle}"
    );
}

struct Args {
    rom: PathBuf,
    state: PathBuf,
    fresh: bool,
    script: String,
    shot: Option<PathBuf>,
    peek: bool,
    peekhex: Option<(u16, u16)>,
    journal: Option<PathBuf>,
}

/// `gb-agent export --journal <dir> --format advice|policy --out <file>`
fn run_export() -> Result<()> {
    let mut journal = None;
    let mut format = String::from("advice");
    let mut out_path = None;
    let mut it = std::env::args().skip(2);
    while let Some(a) = it.next() {
        let mut val = || it.next().ok_or_else(|| anyhow!("missing value for {a}"));
        match a.as_str() {
            "--journal" => journal = Some(PathBuf::from(val()?)),
            "--format" => format = val()?,
            "--out" => out_path = Some(PathBuf::from(val()?)),
            other => bail!("unknown export arg: {other}"),
        }
    }
    let journal = journal.ok_or_else(|| anyhow!("export requires --journal <dir>"))?;
    let out_path = out_path.ok_or_else(|| anyhow!("export requires --out <file>"))?;
    let mut out = std::fs::File::create(&out_path)?;
    let n = match format.as_str() {
        "advice" => gb_tui::export::export_advice(&journal, &mut out)?,
        "policy" => gb_tui::export::export_policy(&journal, &mut out)?,
        other => bail!("unknown format {other} (want advice|policy)"),
    };
    println!("exported {n} {format} records -> {}", out_path.display());
    Ok(())
}

fn parse_args() -> Result<Args> {
    let mut args = Args {
        rom: PathBuf::new(),
        state: PathBuf::new(),
        fresh: false,
        script: String::new(),
        shot: None,
        peek: false,
        peekhex: None,
        journal: None,
    };
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        let mut val = || it.next().ok_or_else(|| anyhow!("missing value for {a}"));
        match a.as_str() {
            "--rom" => args.rom = PathBuf::from(val()?),
            "--state" => args.state = PathBuf::from(val()?),
            "--new" => args.fresh = true,
            "--script" => args.script = val()?,
            "--shot" => args.shot = Some(PathBuf::from(val()?)),
            "--peek" => args.peek = true,
            "--journal" => args.journal = Some(PathBuf::from(val()?)),
            "--peekhex" => {
                let v = val()?;
                let (a, l) = v
                    .split_once(':')
                    .ok_or_else(|| anyhow!("--peekhex wants ADDR:LEN"))?;
                let addr = u16::from_str_radix(a.trim_start_matches("0x"), 16)?;
                args.peekhex = Some((addr, l.parse()?));
            }
            other => bail!("unknown arg: {other}"),
        }
    }
    if args.rom.as_os_str().is_empty() || args.state.as_os_str().is_empty() {
        bail!("required: --rom <rom> --state <file>");
    }
    Ok(args)
}

/// `gb-agent play --rom R --state S --goal "..." [--backend ollama|claude]
///  [--model NAME] [--steps N] [--journal DIR] [--realtime]`
fn run_play() -> Result<()> {
    use gb_tui::autopilot::Driver;
    use gb_tui::autopilot::planner::{PlannerEvent, run_planner_capped};
    use gb_tui::copilot;
    use gb_tui::emu::{EmuConfig, EmuHandle};
    use gb_tui::journal::Journal;
    use std::sync::atomic::AtomicBool;
    use std::sync::{Arc, Mutex};

    let mut rom = None;
    let mut state = None;
    let mut goal = String::from("make progress toward the next objective");
    let mut backend = String::from("ollama");
    let mut model = None;
    let mut steps: usize = 50;
    let mut journal_dir = PathBuf::from("journal");
    let mut realtime = false;
    let mut it = std::env::args().skip(2);
    while let Some(a) = it.next() {
        let mut val = || it.next().ok_or_else(|| anyhow!("missing value for {a}"));
        match a.as_str() {
            "--rom" => rom = Some(PathBuf::from(val()?)),
            "--state" => state = Some(PathBuf::from(val()?)),
            "--goal" => goal = val()?,
            "--backend" => backend = val()?,
            "--model" => model = Some(val()?),
            "--steps" => steps = val()?.parse()?,
            "--journal" => journal_dir = PathBuf::from(val()?),
            "--realtime" => realtime = true,
            other => bail!("unknown play arg: {other}"),
        }
    }
    let rom_path = rom.ok_or_else(|| anyhow!("play requires --rom"))?;
    let state_path = state.ok_or_else(|| anyhow!("play requires --state"))?;

    let mut core = GbCore::new();
    core.load_rom(&std::fs::read(&rom_path)?, None)?;
    core.load_state(&std::fs::read(&state_path)?)?;
    let handle = EmuHandle::spawn(
        Box::new(core),
        EmuConfig {
            frame_duration: if realtime {
                std::time::Duration::from_nanos(16_742_706)
            } else {
                std::time::Duration::from_micros(200)
            },
            ring: None,
            rom_path: None,
            autosave: Some(state_path.clone()),
        },
    );
    let abort = Arc::new(AtomicBool::new(false));
    let driver = Driver::new(
        handle.controller(),
        Arc::clone(&abort),
        PathBuf::from("run/maps"),
    );
    let journal = Arc::new(Mutex::new(Journal::create(&journal_dir)?));
    let (tx, rx) = std::sync::mpsc::channel();

    let mut cfg = copilot::Config::load();
    if let Some(m) = model {
        cfg.model = m;
    }
    let brain = if backend == "claude" {
        "claude CLI".to_string()
    } else {
        cfg.model.clone()
    };
    println!("play: backend={backend} brain={brain} goal={goal:?}");

    std::thread::spawn(move || {
        for ev in rx {
            match ev {
                PlannerEvent::Decided { action, outcome } => println!("  {action} -> {outcome}"),
                PlannerEvent::Message(m) => println!("  {m}"),
                PlannerEvent::Finished(r) => println!("finished: {r}"),
            }
        }
    });

    match backend.as_str() {
        "ollama" => run_planner_capped(
            |sys, user, shot| {
                let img = shot
                    .filter(|_| cfg.vision)
                    .and_then(|p| std::fs::read(p).ok());
                copilot::ask_blocking_json(&cfg, sys, user.clone(), img)
            },
            &driver,
            Arc::clone(&journal),
            goal,
            tx,
            steps,
        ),
        "claude" => run_planner_capped(
            |sys, user, _shot| ask_claude_cli(sys, user),
            &driver,
            Arc::clone(&journal),
            goal,
            tx,
            steps,
        ),
        other => bail!("unknown backend {other} (want ollama|claude)"),
    }

    // Persist the resulting state back to the state file.
    handle.send(gb_tui::emu::EmuCommand::SaveStateTo(state_path.clone()));
    std::thread::sleep(std::time::Duration::from_millis(300));
    handle.stop();
    println!("state -> {}", state_path.display());
    Ok(())
}

/// Ask Claude through the local `claude` CLI (reuses the existing login).
fn ask_claude_cli(system: &str, user: String) -> Result<String> {
    use std::io::Write;
    use std::process::{Command, Stdio};
    let mut child = Command::new("claude")
        .args(["-p", "--output-format", "text"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .context("failed to launch claude CLI — is Claude Code installed?")?;
    child
        .stdin
        .as_mut()
        .expect("piped stdin")
        .write_all(format!("{system}\n\n{user}").as_bytes())?;
    let out = child.wait_with_output()?;
    if !out.status.success() {
        bail!("claude CLI exited with {}", out.status);
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

fn main() -> Result<()> {
    if std::env::args().nth(1).as_deref() == Some("export") {
        return run_export();
    }
    if std::env::args().nth(1).as_deref() == Some("play") {
        return run_play();
    }
    let args = parse_args()?;
    let ops = parse_script(&args.script)?;

    let rom = std::fs::read(&args.rom).context("reading ROM")?;
    let mut core = GbCore::new();
    core.load_rom(&rom, None)?;

    if args.fresh {
        // run past the boot logo so the game is in charge
        run_frames(&mut core, 240);
    } else {
        let state = std::fs::read(&args.state).context("reading state file")?;
        core.load_state(&state)?;
    }

    let frames = exec(&mut core, &ops);
    // settle one frame so the framebuffer reflects the final state
    run_frames(&mut core, 1);

    std::fs::write(&args.state, core.save_state()?)?;

    if let Some(shot) = &args.shot {
        write_png(shot, &core.framebuffer().rgb)?;
    }
    if args.peek {
        print_peeks(&core);
    }
    if let Some((addr, len)) = args.peekhex {
        let bytes: Vec<String> = (0..len)
            .map(|i| format!("{:02x}", core.peek(addr.wrapping_add(i))))
            .collect();
        println!("hex@{addr:#06x}: {}", bytes.join(" "));
    }
    if let Some(dir) = &args.journal {
        use gb_tui::gamestate::GameState;
        use gb_tui::journal::{EventKind, Journal, Source};
        let mut j = Journal::create(dir)?;
        let gs = GameState::read(&core);
        j.log(
            Source::Agent,
            frames,
            gs.to_json(),
            EventKind::Note {
                text: format!("script: {}", args.script),
            },
        );
    }
    println!("ok: ran {frames} frames, state -> {}", args.state.display());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_script_tokens() {
        let ops = parse_script("up:16 a:2 wait:30 mash-a:5").unwrap();
        assert_eq!(
            ops,
            vec![
                Op::Hold(Button::Up, 16),
                Op::Hold(Button::A, 2),
                Op::Wait(30),
                Op::MashA(5),
            ]
        );
        assert!(parse_script("fly:10").is_err());
        assert!(parse_script("up").is_err());
    }
}
