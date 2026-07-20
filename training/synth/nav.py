"""Synthetic navigation decisions: goal -> correct walk_to target.

Ground truth from map objects (warp positions) and headers (connections).
Teaches destination knowledge; the harness BFS handles pathfinding.
"""

import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import context as C
import prompts

ROOT = Path(__file__).resolve().parent.parent.parent
OBJ = ROOT / "assets/gamedata/maps/objects"
HDR = ROOT / "assets/gamedata/maps/headers"
rng = random.Random(11)

FRIENDLY = {
    "POKECENTER": "Pokémon Center", "MART": "Poké Mart", "GYM": "Gym",
    "POKECENTER".lower(): "Pokémon Center",
}


def load_maps():
    maps = {}
    for f in OBJ.glob("*.asm"):
        name = f.stem
        warps = []
        for m in re.finditer(r'warp_event\s+(\d+),\s*(\d+),\s*(\w+),\s*(\d+)', f.read_text()):
            x, y, dest, _ = m.groups()
            if dest not in ("LAST_MAP",):
                warps.append((int(x), int(y), dest))
        hdr = HDR / f"{name}.asm"
        conns = []
        if hdr.exists():
            for c in re.finditer(r'connection\s+(\w+),\s*(\w+),', hdr.read_text()):
                conns.append((c.group(1), c.group(2)))
        if warps or conns:
            maps[name] = {"warps": warps, "conns": conns}
    return maps


def pretty_dest(dest: str) -> str:
    d = dest.replace("_", " ").title()
    d = d.replace("Pokecenter", "Pokémon Center").replace("Mart", "Poké Mart")
    return d


THINK_WARP = [
    "The {d} entrance on {m} is the warp at ({x},{y}); walking onto it enters.",
    "FACTS/map data: {m} has a warp to {d} at ({x},{y}). Head there.",
    "To enter {d} from {m}, the door tile is ({x},{y}).",
]


def generate(n=1500):
    maps = load_maps()
    names = sorted(maps)
    out = []
    while len(out) < n:
        name = rng.choice(names)
        info = maps[name]
        if info["warps"] and (not info["conns"] or rng.random() < 0.8):
            x, y, dest = rng.choice(info["warps"])
            d = pretty_dest(dest)
            px, py = max(0, x + rng.randint(-8, 8)), max(0, y + rng.randint(-8, 8))
            facts = C.map_facts(name)
            state = f"Overworld on map {name} at ({px},{py}). No battle."
            goal = rng.choice([f"Enter the {d}.", f"Go to {d}.",
                               f"Get inside {d} on {name}."])
            think = rng.choice(THINK_WARP).format(d=d, m=name, x=x, y=y)
            act = json.dumps({"action": "walk_to", "x": x, "y": y})
            kind = "nav_warp"
        else:
            direction, dest = rng.choice(info["conns"])
            d = pretty_dest(dest)
            px, py = rng.randint(4, 30), rng.randint(4, 30)
            edge = {"north": (px, 0), "south": (px, 35), "west": (0, py), "east": (39, py)}[direction]
            facts = C.map_facts(name)
            state = f"Overworld on map {name} at ({px},{py}). No battle."
            goal = f"Travel to {d}."
            think = f"{d} connects to {name} on the {direction} side; walk to the {direction} edge and cross."
            act = json.dumps({"action": "walk_to", "x": edge[0], "y": edge[1]})
            kind = "nav_connection"
        ex = prompts.format_example(facts, state, goal, think, act)
        ex["meta"] = {"kind": kind, "map": name}
        out.append(ex)
    return out


if __name__ == "__main__":
    xs = generate()
    print(len(xs), "nav examples")
    print(xs[0]["messages"][1]["content"][:400])
    print(xs[0]["messages"][2]["content"])
