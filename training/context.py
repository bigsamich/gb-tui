"""Deterministic RAG context builder for the Pokémon Red player model.

Keyed (not fuzzy) retrieval over assets/json. Used by BOTH the dataset
builder and the serving shim so train == inference exactly.
"""

import json
from functools import lru_cache
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"


@lru_cache(maxsize=1)
def _db():
    j = ASSETS / "json"
    pokemon = {p["name"].upper(): p for p in json.loads((j / "pokemon.json").read_text())["pokemon"]}
    moves = {m["name"]: m for m in json.loads((j / "moves.json").read_text())["moves"]}
    chart = json.loads((j / "typechart.json").read_text())["matchups"]
    enc = json.loads((j / "encounters.json").read_text())
    mult = {}
    for m in chart:
        mult[(m["attacker"], m["defender"])] = m["multiplier"]
    return pokemon, moves, mult, enc


def type_multiplier(move_type: str, defender_types: list[str]) -> float:
    _, _, mult, _ = _db()
    x = 1.0
    for dt in dict.fromkeys(defender_types):  # unique, order-kept
        x *= mult.get((move_type, dt), 1.0)
    return x


def mon(species: str):
    pokemon, _, _, _ = _db()
    return pokemon.get(species.upper().replace(" ", "_"))


def move(name: str):
    _, moves, _, _ = _db()
    return moves.get(name.upper().replace(" ", "_"))


def score_move(move_name: str, attacker_types: list[str], defender_types: list[str]) -> float:
    """Damage proxy: power * type-multiplier * STAB * accuracy. 0 for status moves."""
    m = move(move_name)
    if not m or m["power"] == 0:
        return 0.0
    x = type_multiplier(m["type"], defender_types)
    stab = 1.5 if m["type"] in attacker_types else 1.0
    return m["power"] * x * stab * (m["accuracy"] / 255.0)


def best_move(move_names: list[str], attacker_species: str, defender_species: str):
    """Return (best_move_name, ranked list of (name, score, mult))."""
    a, d = mon(attacker_species), mon(defender_species)
    if not a or not d:
        return None, []
    ranked = []
    for name in move_names:
        m = move(name)
        if not m:
            continue
        ranked.append((name, score_move(name, a["types"], d["types"]),
                       type_multiplier(m["type"], d["types"]) if m["power"] else None))
    ranked.sort(key=lambda t: -t[1])
    return (ranked[0][0] if ranked else None), ranked


# ---------------- FACTS block builders ----------------

def battle_facts(enemy_species: str, our_species: str, our_moves: list[str]) -> str:
    """FACTS block for an active battle."""
    e, o = mon(enemy_species), mon(our_species)
    if not e:
        return ""
    lines = [f"Enemy {enemy_species}: type {'/'.join(e['types'])}, "
             f"base spd {e['base']['spd']}, catch rate {e['catch_rate']}."]
    if o:
        for name in our_moves:
            m = move(name)
            if not m:
                continue
            if m["power"] == 0:
                lines.append(f"- {name}: status ({m['effect'].removeprefix('EFFECT_').lower()}), pp {m['pp']}")
            else:
                x = type_multiplier(m["type"], e["types"])
                tag = {0.0: "NO EFFECT", 0.25: "0.25x", 0.5: "0.5x", 1.0: "1x",
                       2.0: "2x SUPER", 4.0: "4x SUPER"}.get(x, f"{x}x")
                stab = " +STAB" if m["type"] in o["types"] else ""
                lines.append(f"- {name}: {m['type']} pow {m['power']} acc {m['accuracy']} -> {tag}{stab}")
    return "\n".join(lines)


import re as _re


@lru_cache(maxsize=256)
def map_warps(map_name: str) -> list[tuple[int, int, str]]:
    """[(x, y, destination)] from the map's object file."""
    f = ASSETS / "gamedata" / "maps" / "objects" / f"{map_name}.asm"
    if not f.exists():
        return []
    out = []
    for m in _re.finditer(r'warp_event\s+(\d+),\s*(\d+),\s*(\w+),\s*\d+', f.read_text()):
        x, y, dest = int(m.group(1)), int(m.group(2)), m.group(3)
        if dest != "LAST_MAP":
            d = dest.replace("_", " ").title().replace("Pokecenter", "Pokémon Center") \
                    .replace("Mart", "Poké Mart")
            out.append((x, y, d))
    return out


def map_facts(map_name: str) -> str:
    """FACTS block for overworld: doors/warps + encounter table for the map."""
    _, _, _, enc = _db()
    lines = []
    warps = map_warps(map_name)
    if warps:
        seen_dest = {}
        for x, y, d in warps:
            seen_dest.setdefault(d, (x, y))
        lines.append("Doors/exits on this map: " +
                     "; ".join(f"{d} at ({x},{y})" for d, (x, y) in seen_dest.items()) + ".")
    e = enc.get(map_name)
    if e and "grass" in e:
        g = e["grass"]
        seen = {}
        for row in g["mons"]:
            seen.setdefault(row["species"], []).append(row["level"])
        parts = [f"{sp} L{min(ls)}-{max(ls)}" for sp, ls in seen.items()]
        lines.append(f"Wild grass (rate {g['rate']}): " + ", ".join(parts) + ".")
    elif not warps:
        lines.append(f"{map_name}: no notable map data.")
    return "\n".join(lines)


def party_facts(species: str, level: int) -> str:
    """Upcoming level-up moves and evolution info for one party member."""
    p = mon(species)
    if not p:
        return ""
    nxt = [f"{l['move']}@L{l['level']}" for l in p["learnset"] if l["level"] > level][:3]
    ev = f" Evolves: {p['evolutions'][0]}." if p["evolutions"] else ""
    up = f" Next moves: {', '.join(nxt)}." if nxt else ""
    return f"{species} (type {'/'.join(p['types'])}).{up}{ev}"


def build_facts(state: dict) -> str:
    """Assemble the FACTS block from a state dict.

    Expected keys (all optional): in_battle(bool), enemy_species, our_species,
    our_moves(list), map_name, party(list of {species, level}).
    """
    out = []
    if state.get("in_battle") and state.get("enemy_species"):
        out.append(battle_facts(state["enemy_species"],
                                state.get("our_species", ""),
                                state.get("our_moves", [])))
    elif state.get("map_name"):
        out.append(map_facts(state["map_name"]))
    for pm in state.get("party", [])[:2]:
        f = party_facts(pm["species"], pm.get("level", 100))
        if f:
            out.append(f)
    return "\n".join(x for x in out if x)


if __name__ == "__main__":
    # smoke test
    bm, ranked = best_move(["THUNDERSHOCK", "GROWL", "THUNDER_WAVE"], "PIKACHU", "STARYU")
    assert bm == "THUNDERSHOCK", ranked
    print("best vs STARYU:", bm)
    print(battle_facts("STARYU", "PIKACHU", ["THUNDERSHOCK", "GROWL", "THUNDER_WAVE"]))
    print(map_facts("Route4"))
    print(party_facts("PIKACHU", 11))
