use anyhow::{Result, anyhow};
use boytacean::gb::{AudioProvider, GameBoy, GameBoyMode};
use boytacean::pad::PadKey;
use boytacean::ppu::{DISPLAY_HEIGHT, DISPLAY_WIDTH};
use boytacean::state::{SaveStateFormat, StateManager};
use std::cell::RefCell;

use super::{Button, EmulatorCore, Frame, RomInfo};

pub struct GbCore {
    // boytacean's frame_buffer() takes &mut self (lazy conversion) — the RefCell
    // keeps the trait's &self methods workable. Moved whole to the emu thread,
    // so Send (not Sync) is all that's needed.
    gb: RefCell<GameBoy>,
    has_battery: bool,
}

impl GbCore {
    pub fn new() -> Self {
        Self {
            gb: RefCell::new(GameBoy::new(None)),
            has_battery: false,
        }
    }
}

impl Default for GbCore {
    fn default() -> Self {
        Self::new()
    }
}

impl EmulatorCore for GbCore {
    fn peek(&self, addr: u16) -> u8 {
        self.gb.borrow().mmu_i().read(addr)
    }

    fn load_rom(&mut self, rom: &[u8], battery_ram: Option<&[u8]>) -> Result<RomInfo> {
        let cgb = rom.len() > 0x143 && rom[0x143] & 0x80 != 0;
        let mode = if cgb {
            GameBoyMode::Cgb
        } else {
            GameBoyMode::Dmg
        };
        let mut gb = GameBoy::new(Some(mode));
        gb.load(true)
            .map_err(|e| anyhow!("boot load failed: {:?}", e))?;
        gb.attach_null_serial();
        gb.load_rom(rom, battery_ram)
            .map_err(|e| anyhow!("ROM load failed: {:?}", e))?;
        let (title, has_battery) = {
            let cart = gb.rom_i();
            (cart.title(), cart.has_battery())
        };
        self.has_battery = has_battery;
        self.gb = RefCell::new(gb);
        Ok(RomInfo {
            title,
            has_battery,
            cgb,
        })
    }

    fn run_frame(&mut self) {
        self.gb.get_mut().next_frame();
    }

    fn framebuffer(&self) -> Frame {
        let mut gb = self.gb.borrow_mut();
        Frame {
            width: DISPLAY_WIDTH,
            height: DISPLAY_HEIGHT,
            rgb: gb.frame_buffer().to_vec(),
        }
    }

    fn set_button(&mut self, button: Button, pressed: bool) {
        let key = match button {
            Button::Up => PadKey::Up,
            Button::Down => PadKey::Down,
            Button::Left => PadKey::Left,
            Button::Right => PadKey::Right,
            Button::A => PadKey::A,
            Button::B => PadKey::B,
            Button::Start => PadKey::Start,
            Button::Select => PadKey::Select,
        };
        let gb = self.gb.get_mut();
        if pressed {
            gb.key_press(key);
        } else {
            gb.key_lift(key);
        }
    }

    fn drain_audio(&mut self, out: &mut Vec<i16>) {
        let gb = self.gb.get_mut();
        out.extend(gb.audio_buffer().iter().copied());
        gb.clear_audio_buffer();
    }

    fn sample_rate(&self) -> u32 {
        self.gb.borrow().apu_i().sampling_rate() as u32
    }

    fn save_state(&self) -> Result<Vec<u8>> {
        StateManager::save(&mut self.gb.borrow_mut(), Some(SaveStateFormat::Bosc), None)
            .map_err(|e| anyhow!("save state failed: {:?}", e))
    }

    fn load_state(&mut self, data: &[u8]) -> Result<()> {
        StateManager::load(data, self.gb.get_mut(), None, None)
            .map_err(|e| anyhow!("load state failed: {:?}", e))
    }

    fn battery_ram(&self) -> Option<Vec<u8>> {
        if !self.has_battery {
            return None;
        }
        Some(self.gb.borrow_mut().ram_data_eager())
    }
}
