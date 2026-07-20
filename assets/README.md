# Pokémon Red — game data knowledge base

Factual game data for Pokémon Red, extracted and parsed from the **[pret/pokered](https://github.com/pret/pokered)** disassembly (the openly-distributed disassembled game). This is game data (stats, movesets, type matchups, maps, encounter tables) — not wiki articles or guides. Bulbapedia's prose/guides were deliberately **not** scraped (copyright + their bot policy); the disassembly is a cleaner, authoritative source anyway.

Version-specific data (wild encounters) is resolved to the **Red** version.

## `WALKTHROUGH.md` — strategy guide

An original, data-grounded strategy guide written for Bobby's Charmander→Pikachu run: all 8 gyms in order with their exact rosters/levels (from the disassembly), recommended team levels, per-gym type strategy, the overworld route path, and required HMs. Not a copy of any third-party walkthrough — it's synthesized from the data files here.

## `json/` — clean, agent-ready datasets

| File | Contents |
|---|---|
| `pokemon.json` | All 151 species: base stats (hp/atk/def/spd/spc), types, catch rate, base exp, starting moves, **level-up learnsets**, evolutions, TM/HM compatibility |
| `moves.json` | All 165 moves: type, power, accuracy, PP, effect |
| `typechart.json` | Type effectiveness — 82 non-neutral matchups (2×, 0.5×, 0×). Any pair not listed is 1× neutral. Includes the Gen-1 Ghost-vs-Psychic 0× quirk |
| `encounters.json` | Wild grass/water encounter tables per map (species, levels, encounter rate) — Red version |

Example (why this matters for the current grind): `encounters.json` → `Route4` shows Rattata/Spearow/Ekans L6–12; `pokemon.json` → `pikachu` learnset shows Quick Attack @L16, Thunderbolt via TM.

## `gamedata/` — raw disassembly source (for anything not yet parsed)

- `pokemon/base_stats/*.asm` (151), `pokemon/evos_moves.asm`, `pokemon/names.asm`
- `moves/moves.asm`, `moves/names.asm`, `moves/tmhm_moves.asm`
- `types/type_matchups.asm`, `types/names.asm`
- `wild/maps/*.asm` (59), `wild/probabilities.asm`
- `items/names.asm`, `items/prices.asm`
- `maps/` — **225 `.blk` map layouts**, 19 `.bst` tilesets/blocksets, 223 `objects/*.asm` (warps/trainers/items), 223 `headers/*.asm` (connections/tileset), plus `meta/` (map_constants, collision_tile_ids, ledge_tiles)

## Notes
- Map `.blk` + blockset `.bst` + `collision_tile_ids.asm` are what the headless agent's BFS navigation consumes (see the collision-model memory).
- To re-parse or extend, the parser lives in the session scratchpad; re-run against `gamedata/` to regenerate `json/`.
