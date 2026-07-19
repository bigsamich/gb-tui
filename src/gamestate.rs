//! Typed reader of Pokémon Red (US) game state via emulator RAM peeks.
//!
//! Addresses were verified during the scripted playthrough documented in
//! run/PLAYBOOK.md. This module is deliberately Red-specific.

use crate::core::EmulatorCore;
use serde::{Deserialize, Serialize};

const PARTY_COUNT: u16 = 0xD163;
const PARTY_SPECIES: u16 = 0xD164;
const PARTY_MON0: u16 = 0xD16B;
const PARTY_STRIDE: u16 = 44;
const MAP_ID: u16 = 0xD35E;
const POS_Y: u16 = 0xD361;
const POS_X: u16 = 0xD362;
const BADGES: u16 = 0xD356;
const MONEY: u16 = 0xD347;
const BAG_COUNT: u16 = 0xD31D;
const BATTLE_KIND: u16 = 0xD057;
const ENEMY_SPECIES: u16 = 0xCFE5;
const ENEMY_HP: u16 = 0xCFE6;
const ENEMY_LEVEL: u16 = 0xCFF3;
const MENU_CURSOR: u16 = 0xCC26;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MoveSlot {
    pub id: u8,
    pub name: String,
    pub pp: u8,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PartyMon {
    pub species: u8,
    pub name: String,
    pub level: u8,
    pub hp: u16,
    pub max_hp: u16,
    pub status: u8,
    pub moves: Vec<MoveSlot>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BattleState {
    pub kind: u8,
    pub enemy_species: u8,
    pub enemy_name: String,
    pub enemy_level: u8,
    pub enemy_hp: u16,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BagItem {
    pub id: u8,
    pub name: String,
    pub count: u8,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GameState {
    pub map: u8,
    pub map_name: String,
    pub x: u8,
    pub y: u8,
    pub money: u32,
    pub badges: u8,
    pub party: Vec<PartyMon>,
    pub battle: Option<BattleState>,
    pub bag: Vec<BagItem>,
    pub menu_cursor: u8,
}

impl GameState {
    pub fn read(core: &dyn EmulatorCore) -> GameState {
        let peek = |a: u16| core.peek(a);
        let peek16 = |a: u16| ((peek(a) as u16) << 8) | peek(a + 1) as u16;

        let bcd = |b: u8| ((b >> 4) * 10 + (b & 0x0F)) as u32;
        let money = bcd(peek(MONEY)) * 10_000 + bcd(peek(MONEY + 1)) * 100 + bcd(peek(MONEY + 2));

        let count = peek(PARTY_COUNT).min(6);
        let mut party = Vec::new();
        for i in 0..count {
            let base = PARTY_MON0 + i as u16 * PARTY_STRIDE;
            let species = peek(PARTY_SPECIES + i as u16);
            let mut moves = Vec::new();
            for m in 0..4u16 {
                let id = peek(base + 8 + m);
                if id != 0 {
                    moves.push(MoveSlot {
                        id,
                        name: move_name(id),
                        pp: peek(base + 0x1D + m) & 0x3F,
                    });
                }
            }
            party.push(PartyMon {
                species,
                name: species_name(species),
                level: peek(base + 0x21),
                hp: peek16(base + 1),
                max_hp: peek16(base + 0x22),
                status: peek(base + 4),
                moves,
            });
        }

        let kind = peek(BATTLE_KIND);
        let battle = if kind != 0 {
            let sp = peek(ENEMY_SPECIES);
            Some(BattleState {
                kind,
                enemy_species: sp,
                enemy_name: species_name(sp),
                enemy_level: peek(ENEMY_LEVEL),
                enemy_hp: peek16(ENEMY_HP),
            })
        } else {
            None
        };

        let mut bag = Vec::new();
        let n = peek(BAG_COUNT).min(20);
        for i in 0..n as u16 {
            let id = peek(BAG_COUNT + 1 + i * 2);
            if id == 0xFF {
                break;
            }
            bag.push(BagItem {
                id,
                name: item_name(id),
                count: peek(BAG_COUNT + 2 + i * 2),
            });
        }

        let map = peek(MAP_ID);
        GameState {
            map,
            map_name: map_name(map),
            x: peek(POS_X),
            y: peek(POS_Y),
            money,
            badges: peek(BADGES),
            party,
            battle,
            bag,
            menu_cursor: peek(MENU_CURSOR),
        }
    }

    pub fn prompt_text(&self) -> String {
        let mut s = format!(
            "Location: {} (map {}) at ({},{}). Money: {}. Badges: {}.\n",
            self.map_name, self.map, self.x, self.y, self.money, self.badges
        );
        for m in &self.party {
            let moves: Vec<String> = m
                .moves
                .iter()
                .map(|mv| format!("{} {}PP", mv.name, mv.pp))
                .collect();
            let status = match m.status {
                0 => String::new(),
                0x08 => " POISONED".into(),
                other => format!(" status=0x{other:02X}"),
            };
            s.push_str(&format!(
                "{} L{} {}/{}HP{} [{}]\n",
                m.name,
                m.level,
                m.hp,
                m.max_hp,
                status,
                moves.join(", ")
            ));
        }
        if let Some(b) = &self.battle {
            let kind = if b.kind == 2 { "trainer" } else { "wild" };
            s.push_str(&format!(
                "IN BATTLE ({kind}) vs {} L{} {}HP\n",
                b.enemy_name, b.enemy_level, b.enemy_hp
            ));
        }
        if !self.bag.is_empty() {
            let items: Vec<String> = self
                .bag
                .iter()
                .map(|i| format!("{} x{}", i.name, i.count))
                .collect();
            s.push_str(&format!("Bag: {}\n", items.join(", ")));
        }
        s
    }

    pub fn to_json(&self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or(serde_json::Value::Null)
    }
}

pub fn species_name(id: u8) -> String {
    match id {
        0x01 => "RHYDON",
        0x03 => "NIDORAN-M",
        0x04 => "CLEFAIRY",
        0x05 => "SPEAROW",
        0x0F => "NIDORAN-F",
        0x22 => "ONIX",
        0x24 => "PIDGEY",
        0x39 => "MANKEY",
        0x54 => "PIKACHU",
        0x60 => "SANDSHREW",
        0x64 => "JIGGLYPUFF",
        0x6B => "ZUBAT",
        0x6C => "EKANS",
        0x6D => "PARAS",
        0x70 => "WEEDLE",
        0x71 => "KAKUNA",
        0x72 => "BEEDRILL",
        0x7B => "CATERPIE",
        0x7C => "METAPOD",
        0x7D => "BUTTERFREE",
        0x99 => "BULBASAUR",
        0xA5 => "RATTATA",
        0xA9 => "GEODUDE",
        0xB0 => "CHARMANDER",
        0xB1 => "SQUIRTLE",
        0xB2 => "CHARMELEON",
        0xB3 => "WARTORTLE",
        0xB4 => "CHARIZARD",
        other => return format!("SPECIES_{other:02X}"),
    }
    .to_string()
}

pub fn move_name(id: u8) -> String {
    match id {
        1 => "Pound",
        10 => "Scratch",
        16 => "Gust",
        33 => "Tackle",
        39 => "Tail Whip",
        40 => "Poison Sting",
        43 => "Leer",
        45 => "Growl",
        52 => "Ember",
        53 => "Flamethrower",
        81 => "String Shot",
        84 => "Thundershock",
        86 => "Thunder Wave",
        98 => "Quick Attack",
        103 => "Screech",
        106 => "Harden",
        111 => "Defense Curl",
        117 => "Bide",
        other => return format!("MOVE_{other}"),
    }
    .to_string()
}

pub fn item_name(id: u8) -> String {
    match id {
        0x04 => "Poke Ball",
        0x05 => "Town Map",
        0x0B => "Antidote",
        0x14 => "Potion",
        0x1D => "Escape Rope",
        0x46 => "Oak's Parcel",
        other => return format!("ITEM_{other:02X}"),
    }
    .to_string()
}

pub fn map_name(id: u8) -> String {
    match id {
        0 => "Pallet Town",
        1 => "Viridian City",
        2 => "Pewter City",
        12 => "Route 1",
        13 => "Route 2",
        37 => "Bobby's house 1F",
        38 => "Bobby's house 2F",
        39 => "Rival's house",
        40 => "Oak's Lab",
        41 => "Viridian Pokecenter",
        42 => "Viridian Mart",
        47 => "Viridian Forest north gate",
        50 => "Viridian Forest south gate",
        51 => "Viridian Forest",
        54 => "Pewter Gym",
        56 => "Pewter Mart",
        58 => "Pewter Pokecenter",
        other => return format!("MAP_{other}"),
    }
    .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::EmulatorCore;

    #[test]
    fn reads_boulder_badge_checkpoint() {
        let rom = std::path::Path::new("test-roms/pokemon-red.gb");
        let state = std::path::Path::new("run/ck-BOULDER-BADGE.state");
        if !rom.exists() || !state.exists() {
            eprintln!("SKIP: fixtures absent");
            return;
        }
        let mut core = crate::core::gb::GbCore::new();
        core.load_rom(&std::fs::read(rom).unwrap(), None).unwrap();
        core.load_state(&std::fs::read(state).unwrap()).unwrap();
        let gs = GameState::read(&core);
        assert_eq!(gs.map, 54); // Pewter Gym
        assert_eq!(gs.badges, 1);
        assert_eq!(gs.party.len(), 2);
        assert_eq!(gs.party[0].name, "CHARMELEON");
        assert_eq!(gs.party[0].level, 18);
        assert_eq!(gs.party[1].name, "PIKACHU");
        assert!(gs.prompt_text().contains("CHARMELEON L18"));
        assert!(gs.money > 0);
    }

    #[test]
    fn name_tables_fall_back() {
        assert_eq!(species_name(0x54), "PIKACHU");
        assert_eq!(species_name(0xEE), "SPECIES_EE");
        assert_eq!(move_name(52), "Ember");
        assert_eq!(move_name(200), "MOVE_200");
        assert_eq!(map_name(51), "Viridian Forest");
    }
}
