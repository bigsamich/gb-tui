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
use std::path::PathBuf;

const FRAME_W: usize = 160;
const FRAME_H: usize = 144;
const SCALE: usize = 3;

#[derive(Debug, PartialEq)]
enum Op {
    Hold(Button, u32),
    Wait(u32),
    MashA(u32),
}

fn parse_script(script: &str) -> Result<Vec<Op>> {
    let mut ops = Vec::new();
    for tok in script.split_whitespace() {
        let (name, n) = tok
            .split_once(':')
            .ok_or_else(|| anyhow!("bad token (want name:count): {tok}"))?;
        let n: u32 = n.parse().with_context(|| format!("bad count in {tok}"))?;
        let op = match name.to_ascii_lowercase().as_str() {
            "up" => Op::Hold(Button::Up, n),
            "down" => Op::Hold(Button::Down, n),
            "left" => Op::Hold(Button::Left, n),
            "right" => Op::Hold(Button::Right, n),
            "a" => Op::Hold(Button::A, n),
            "b" => Op::Hold(Button::B, n),
            "start" => Op::Hold(Button::Start, n),
            "select" => Op::Hold(Button::Select, n),
            "wait" => Op::Wait(n),
            "mash-a" => Op::MashA(n),
            other => bail!("unknown op: {other}"),
        };
        ops.push(op);
    }
    Ok(ops)
}

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
