//! Gen-1 map collision decoding (.blk block maps + .bst blocksets) and BFS
//! pathfinding. Assets are fetched by `run/fetch-maps.sh` (never committed).

use std::collections::VecDeque;
use std::path::Path;

const OVERWORLD_COLL: &[u8] = &[
    0x00, 0x10, 0x1B, 0x20, 0x21, 0x23, 0x2C, 0x2D, 0x2E, 0x30, 0x31, 0x33, 0x39, 0x3C, 0x3E, 0x52,
    0x54, 0x58, 0x5B,
];
const FOREST_COLL: &[u8] = &[
    0x1E, 0x20, 0x2E, 0x30, 0x34, 0x37, 0x39, 0x3A, 0x40, 0x51, 0x52, 0x5A, 0x5C, 0x5E, 0x5F,
];

/// map id -> (blk file, bst file, collision list, width blocks, height blocks)
fn map_meta(map_id: u8) -> Option<(&'static str, &'static str, &'static [u8], usize, usize)> {
    Some(match map_id {
        0 => ("PalletTown.blk", "overworld.bst", OVERWORLD_COLL, 10, 9),
        1 => ("ViridianCity.blk", "overworld.bst", OVERWORLD_COLL, 20, 18),
        2 => ("PewterCity.blk", "overworld.bst", OVERWORLD_COLL, 20, 18),
        12 => ("Route1.blk", "overworld.bst", OVERWORLD_COLL, 10, 18),
        13 => ("Route2.blk", "overworld.bst", OVERWORLD_COLL, 10, 36),
        51 => ("ViridianForest.blk", "forest.bst", FOREST_COLL, 17, 24),
        _ => return None,
    })
}

pub struct MapGrid {
    pub w: usize,
    pub h: usize,
    walk: Vec<bool>,
}

impl MapGrid {
    pub fn walkable(&self, x: u8, y: u8) -> bool {
        let (x, y) = (x as usize, y as usize);
        x < self.w && y < self.h && self.walk[y * self.w + x]
    }
}

pub fn load(map_id: u8, assets_dir: &Path) -> Option<MapGrid> {
    let (blk_name, bst_name, coll, wb, hb) = map_meta(map_id)?;
    let blk = std::fs::read(assets_dir.join(blk_name)).ok()?;
    let bst = std::fs::read(assets_dir.join(bst_name)).ok()?;
    if blk.len() < wb * hb {
        return None;
    }
    let (tw, th) = (wb * 4, hb * 4);
    let mut tiles = vec![0u8; tw * th];
    for by in 0..hb {
        for bx in 0..wb {
            let block = blk[by * wb + bx] as usize;
            let Some(bytes) = bst.get(block * 16..block * 16 + 16) else {
                continue;
            };
            for (i, t) in bytes.iter().enumerate() {
                tiles[(by * 4 + i / 4) * tw + bx * 4 + i % 4] = *t;
            }
        }
    }
    let (w, h) = (wb * 2, hb * 2);
    let mut walk = vec![false; w * h];
    for y in 0..h {
        for x in 0..w {
            let tile = tiles[(2 * y + 1) * tw + 2 * x];
            walk[y * w + x] = coll.contains(&tile);
        }
    }
    Some(MapGrid { w, h, walk })
}

/// BFS shortest path; returns run-length compressed moves like [('u',3),('l',2)].
pub fn bfs(grid: &MapGrid, start: (u8, u8), goal: (u8, u8)) -> Option<Vec<(char, u8)>> {
    let idx = |x: u8, y: u8| y as usize * grid.w + x as usize;
    let mut prev: Vec<Option<(u8, u8, char)>> = vec![None; grid.w * grid.h];
    let mut seen = vec![false; grid.w * grid.h];
    let mut q = VecDeque::new();
    seen[idx(start.0, start.1)] = true;
    q.push_back(start);
    let dirs: [(i16, i16, char); 4] = [(0, -1, 'u'), (0, 1, 'd'), (-1, 0, 'l'), (1, 0, 'r')];
    while let Some((x, y)) = q.pop_front() {
        if (x, y) == goal {
            break;
        }
        for (dx, dy, d) in dirs {
            let (nx, ny) = (x as i16 + dx, y as i16 + dy);
            if nx < 0 || ny < 0 || nx as usize >= grid.w || ny as usize >= grid.h {
                continue;
            }
            let (nx, ny) = (nx as u8, ny as u8);
            if seen[idx(nx, ny)] || !grid.walkable(nx, ny) {
                continue;
            }
            seen[idx(nx, ny)] = true;
            prev[idx(nx, ny)] = Some((x, y, d));
            q.push_back((nx, ny));
        }
    }
    if !seen[idx(goal.0, goal.1)] || start == goal {
        return if start == goal { Some(vec![]) } else { None };
    }
    let mut steps = Vec::new();
    let mut cur = goal;
    while cur != start {
        let (px, py, d) = prev[idx(cur.0, cur.1)]?;
        steps.push(d);
        cur = (px, py);
    }
    steps.reverse();
    let mut out: Vec<(char, u8)> = Vec::new();
    for d in steps {
        match out.last_mut() {
            Some((last, n)) if *last == d && *n < 250 => *n += 1,
            _ => out.push((d, 1)),
        }
    }
    Some(out)
}

/// Convert compressed moves into an agent op script (16 frames per step).
pub fn moves_to_ops(path: &[(char, u8)]) -> String {
    let mut parts = Vec::new();
    for (d, n) in path {
        let dir = match d {
            'u' => "up",
            'd' => "down",
            'l' => "left",
            'r' => "right",
            _ => continue,
        };
        parts.push(format!("{}:{} wait:10", dir, *n as u32 * 16));
    }
    parts.join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assets() -> Option<std::path::PathBuf> {
        let p = std::path::PathBuf::from("run/maps");
        if p.join("ViridianForest.blk").exists() {
            Some(p)
        } else {
            eprintln!("SKIP: run/maps absent (run run/fetch-maps.sh)");
            None
        }
    }

    #[test]
    fn decodes_viridian_forest() {
        let Some(dir) = assets() else { return };
        let grid = load(51, &dir).unwrap();
        assert_eq!(grid.w, 34);
        assert_eq!(grid.h, 48);
        assert!(grid.walkable(1, 18)); // BC3's exit corridor
        assert!(!grid.walkable(0, 0)); // border trees
    }

    #[test]
    fn bfs_finds_forest_exit_path() {
        let Some(dir) = assets() else { return };
        let grid = load(51, &dir).unwrap();
        let path = bfs(&grid, (15, 19), (1, 0)).expect("path exists");
        let total: u32 = path.iter().map(|(_, n)| *n as u32).sum();
        assert!(total >= 40, "unexpectedly short path: {total}");
    }

    #[test]
    fn moves_compress_to_ops() {
        assert_eq!(
            moves_to_ops(&[('u', 3), ('l', 2)]),
            "up:48 wait:10 left:32 wait:10"
        );
    }
}
