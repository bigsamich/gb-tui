use std::path::{Path, PathBuf};

pub fn scan_roms(dir: &Path) -> Vec<PathBuf> {
    let mut roms: Vec<PathBuf> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .filter(|e| e.file_type().map(|t| t.is_file()).unwrap_or(false))
        .map(|e| e.path())
        .filter(|p| {
            p.extension()
                .and_then(|e| e.to_str())
                .map(|e| {
                    let e = e.to_ascii_lowercase();
                    e == "gb" || e == "gbc"
                })
                .unwrap_or(false)
        })
        .collect();
    roms.sort_by_key(|p| {
        p.file_name()
            .map(|n| n.to_string_lossy().to_lowercase())
            .unwrap_or_default()
    });
    roms
}

pub struct Browser {
    pub dir: PathBuf,
    pub entries: Vec<PathBuf>,
    pub selected: usize,
}

impl Browser {
    pub fn new(dir: PathBuf) -> Self {
        let entries = scan_roms(&dir);
        Self {
            dir,
            entries,
            selected: 0,
        }
    }

    pub fn rescan(&mut self) {
        self.entries = scan_roms(&self.dir);
        self.selected = self.selected.min(self.entries.len().saturating_sub(1));
    }

    pub fn up(&mut self) {
        self.selected = self.selected.saturating_sub(1);
    }

    pub fn down(&mut self) {
        if !self.entries.is_empty() {
            self.selected = (self.selected + 1).min(self.entries.len() - 1);
        }
    }

    pub fn selected_path(&self) -> Option<&Path> {
        self.entries.get(self.selected).map(|p| p.as_path())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn touch(dir: &std::path::Path, name: &str) {
        std::fs::write(dir.join(name), b"x").unwrap();
    }

    #[test]
    fn scan_filters_and_sorts() {
        let tmp = tempfile::tempdir().unwrap();
        touch(tmp.path(), "zelda.GB");
        touch(tmp.path(), "mario.gbc");
        touch(tmp.path(), "notes.txt");
        touch(tmp.path(), "Alpha.gb");
        std::fs::create_dir(tmp.path().join("sub.gb")).unwrap(); // dir: excluded
        let names: Vec<String> = scan_roms(tmp.path())
            .iter()
            .map(|p| p.file_name().unwrap().to_string_lossy().into_owned())
            .collect();
        assert_eq!(names, vec!["Alpha.gb", "mario.gbc", "zelda.GB"]);
    }

    #[test]
    fn navigation_clamps() {
        let tmp = tempfile::tempdir().unwrap();
        touch(tmp.path(), "a.gb");
        touch(tmp.path(), "b.gb");
        let mut b = Browser::new(tmp.path().to_path_buf());
        assert_eq!(b.selected, 0);
        b.up();
        assert_eq!(b.selected, 0);
        b.down();
        assert_eq!(b.selected, 1);
        b.down();
        assert_eq!(b.selected, 1);
        assert!(b.selected_path().unwrap().ends_with("b.gb"));
    }

    #[test]
    fn empty_dir_has_no_selection() {
        let tmp = tempfile::tempdir().unwrap();
        let mut b = Browser::new(tmp.path().to_path_buf());
        assert!(b.selected_path().is_none());
        b.down(); // must not panic
        b.up();
    }
}
