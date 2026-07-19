pub mod gb;

use anyhow::Result;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Button {
    Up,
    Down,
    Left,
    Right,
    A,
    B,
    Start,
    Select,
}

#[derive(Debug, Clone)]
pub struct Frame {
    pub width: usize,
    pub height: usize,
    pub rgb: Vec<u8>,
}

#[derive(Debug, Clone)]
pub struct RomInfo {
    pub title: String,
    pub has_battery: bool,
    pub cgb: bool,
}

pub trait EmulatorCore: Send {
    fn load_rom(&mut self, rom: &[u8], battery_ram: Option<&[u8]>) -> Result<RomInfo>;
    fn run_frame(&mut self);
    fn framebuffer(&self) -> Frame;
    fn set_button(&mut self, button: Button, pressed: bool);
    /// Append interleaved stereo i16 samples and clear the core's buffer.
    fn drain_audio(&mut self, out: &mut Vec<i16>);
    fn sample_rate(&self) -> u32;
    fn save_state(&self) -> Result<Vec<u8>>;
    fn load_state(&mut self, data: &[u8]) -> Result<()>;
    fn battery_ram(&self) -> Option<Vec<u8>>;
}
