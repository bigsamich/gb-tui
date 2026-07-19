use gb_tui::core::gb::GbCore;
use gb_tui::core::{Button, EmulatorCore};
use std::path::Path;

fn checksum(data: &[u8]) -> u64 {
    // FNV-1a
    data.iter().fold(0xcbf29ce484222325u64, |h, b| {
        (h ^ *b as u64).wrapping_mul(0x100000001b3)
    })
}

fn run_frames(core: &mut dyn EmulatorCore, n: usize) {
    for _ in 0..n {
        core.run_frame();
    }
}

#[test]
fn dmg_acid2_renders_stable_frame() {
    let rom = std::fs::read("test-roms/dmg-acid2.gb").expect("committed test rom");
    let mut core = GbCore::new();
    let info = core.load_rom(&rom, None).unwrap();
    assert!(!info.has_battery);
    run_frames(&mut core, 400);
    let f1 = core.framebuffer();
    assert_eq!(f1.width, 160);
    assert_eq!(f1.height, 144);
    assert_eq!(f1.rgb.len(), 160 * 144 * 3);
    // non-blank: more than one distinct pixel value
    let first = &f1.rgb[0..3];
    assert!(f1.rgb.chunks(3).any(|p| p != first), "framebuffer is blank");
    // stable: two consecutive frames of a static test screen are identical
    let c1 = checksum(&f1.rgb);
    run_frames(&mut core, 1);
    let c2 = checksum(&core.framebuffer().rgb);
    assert_eq!(c1, c2);
}

#[test]
fn dmg_acid2_save_state_round_trip() {
    let rom = std::fs::read("test-roms/dmg-acid2.gb").unwrap();
    let mut core = GbCore::new();
    core.load_rom(&rom, None).unwrap();
    run_frames(&mut core, 200);
    let state = core.save_state().unwrap();
    let c_before = checksum(&core.framebuffer().rgb);
    run_frames(&mut core, 60);
    core.load_state(&state).unwrap();
    run_frames(&mut core, 1); // render one frame after restore
    assert_eq!(checksum(&core.framebuffer().rgb), c_before);
}

#[test]
fn core_produces_audio_samples() {
    let rom = std::fs::read("test-roms/dmg-acid2.gb").unwrap();
    let mut core = GbCore::new();
    core.load_rom(&rom, None).unwrap();
    assert_eq!(core.sample_rate(), 44100);
    let mut out = Vec::new();
    for _ in 0..10 {
        core.run_frame();
        core.drain_audio(&mut out);
    }
    // ~10 frames at 44100Hz stereo ≈ 14_700 samples; be generous
    assert!(
        out.len() > 5_000,
        "expected audio samples, got {}",
        out.len()
    );
}

#[test]
fn pokemon_red_boots_and_saves() {
    let path = Path::new("test-roms/pokemon-red.gb");
    if !path.exists() {
        eprintln!("SKIP: test-roms/pokemon-red.gb not present (user-supplied)");
        return;
    }
    let rom = std::fs::read(path).unwrap();
    let mut core = GbCore::new();
    let info = core.load_rom(&rom, None).unwrap();
    assert!(info.title.to_uppercase().contains("POKEMON"));
    assert!(info.has_battery, "Pokemon Red cart has battery-backed RAM");
    run_frames(&mut core, 600); // through the intro copyright screens
    let f = core.framebuffer();
    let first = &f.rgb[0..3];
    assert!(
        f.rgb.chunks(3).any(|p| p != first),
        "blank screen after 600 frames"
    );
    // battery RAM round-trips through load_rom
    let ram = core.battery_ram().expect("battery ram");
    let mut core2 = GbCore::new();
    core2.load_rom(&rom, Some(&ram)).unwrap();
    assert_eq!(core2.battery_ram().unwrap(), ram);
    // buttons don't panic
    core.set_button(Button::Start, true);
    run_frames(&mut core, 5);
    core.set_button(Button::Start, false);
}
