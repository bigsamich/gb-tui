use anyhow::Result;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const FLUSH_INTERVAL: Duration = Duration::from_secs(10);

pub fn sav_path(rom: &Path) -> PathBuf {
    rom.with_extension("sav")
}

pub fn state_path(rom: &Path, slot: u8) -> PathBuf {
    rom.with_extension(format!("st{slot}"))
}

pub fn load_battery(rom: &Path) -> Option<Vec<u8>> {
    std::fs::read(sav_path(rom)).ok()
}

pub struct BatterySaver {
    path: PathBuf,
    last_written: Option<Vec<u8>>,
    last_flush: Instant,
}

impl BatterySaver {
    pub fn new(rom: &Path) -> Self {
        Self {
            path: sav_path(rom),
            last_written: None,
            last_flush: Instant::now(),
        }
    }

    /// Writes when forced or when the flush interval elapsed, and only if the
    /// bytes actually changed. Returns whether a write happened.
    pub fn maybe_flush(&mut self, ram: Option<&[u8]>, force: bool) -> Result<bool> {
        let Some(ram) = ram else { return Ok(false) };
        if !force && self.last_flush.elapsed() < FLUSH_INTERVAL {
            return Ok(false);
        }
        if self.last_written.as_deref() == Some(ram) {
            self.last_flush = Instant::now();
            return Ok(false);
        }
        std::fs::write(&self.path, ram)?;
        self.last_written = Some(ram.to_vec());
        self.last_flush = Instant::now();
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn paths_derive_from_rom() {
        assert_eq!(
            sav_path(Path::new("/roms/red.gb")),
            Path::new("/roms/red.sav")
        );
        assert_eq!(
            state_path(Path::new("/roms/red.gb"), 3),
            Path::new("/roms/red.st3")
        );
    }

    #[test]
    fn battery_saver_writes_once_per_change() {
        let dir = tempfile::tempdir().unwrap();
        let rom = dir.path().join("game.gb");
        std::fs::write(&rom, b"rom").unwrap();
        let mut saver = BatterySaver::new(&rom);
        // force write
        assert!(saver.maybe_flush(Some(b"AAAA"), true).unwrap());
        assert_eq!(std::fs::read(sav_path(&rom)).unwrap(), b"AAAA");
        // unchanged + forced -> no write
        assert!(!saver.maybe_flush(Some(b"AAAA"), true).unwrap());
        // changed + forced -> write
        assert!(saver.maybe_flush(Some(b"BBBB"), true).unwrap());
        assert_eq!(std::fs::read(sav_path(&rom)).unwrap(), b"BBBB");
        // changed but unforced and <10s elapsed -> no write
        assert!(!saver.maybe_flush(Some(b"CCCC"), false).unwrap());
        // no battery -> no write
        assert!(!saver.maybe_flush(None, true).unwrap());
    }

    #[test]
    fn load_battery_reads_existing_sav() {
        let dir = tempfile::tempdir().unwrap();
        let rom = dir.path().join("game.gb");
        assert!(load_battery(&rom).is_none());
        std::fs::write(dir.path().join("game.sav"), b"SAVE").unwrap();
        assert_eq!(load_battery(&rom).unwrap(), b"SAVE");
    }
}
