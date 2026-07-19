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

fn main() -> Result<()> {
    if std::env::args().nth(1).as_deref() == Some("export") {
        return run_export();
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
