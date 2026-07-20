"""Synthetic battle decisions with ground-truth labels.

Move choice: (enemy species x our species/level/moveset) -> argmax damage proxy.
Catch/flee: wild encounters vs the run's catch policy.
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
rng = random.Random(7)


def moveset_at(species: str, level: int) -> list[str]:
    p = C.mon(species)
    if not p:
        return []
    ms = [m for m in p["start_moves"]]
    for l in p["learnset"]:
        if l["level"] <= level:
            ms.append(l["move"])
    # keep last 4 (game replaces oldest by prompt; approximation)
    out = []
    for m in ms:
        if m in out:
            continue
        out.append(m)
    return out[-4:]


def enemy_pool():
    """Species the run actually meets: wild tables + trainer parties."""
    _, _, _, enc = C._db()
    species = set()
    for m in enc.values():
        for k in ("grass", "water"):
            if k in m:
                species.update(r["species"] for r in m[k]["mons"])
    t = (ROOT / "assets/gamedata/trainers/parties.asm").read_text()
    species.update(re.findall(r'\b([A-Z][A-Z0-9_]{2,})\b(?=,|\s*,)', t) and
                   re.findall(r'db \$FF(?:[^\n]*)', t) and set())
    for line in t.splitlines():
        for m in re.finditer(r'\b(?:db\s+\$?\w+,\s*)?(\d+),\s*([A-Z][A-Z0-9_]+)', line):
            sp = m.group(2)
            if C.mon(sp):
                species.add(sp)
    return sorted(s for s in species if C.mon(s))


THINK_MOVE = [
    "{enemy} is {etypes}. {best} is {mult} here{stab}, the strongest option.",
    "Best damage vs {etypes} {enemy}: {best} ({mult}{stab}). Status moves waste a turn.",
    "Checking FACTS: {best} hits {enemy} at {mult}{stab}; nothing else scores higher.",
]

def gen_move_choice(n=4000):
    ours = [("PIKACHU", l) for l in (9, 12, 15, 18, 20, 25)] + \
           [("CHARMANDER", 8), ("CHARMELEON", 18), ("CHARMELEON", 25),
            ("CHARMELEON", 30), ("CHARIZARD", 40), ("CHARIZARD", 50)]
    enemies = enemy_pool()
    out = []
    while len(out) < n:
        sp, lvl = rng.choice(ours)
        enemy = rng.choice(enemies)
        elvl = max(2, min(55, lvl + rng.randint(-4, 4)))
        ms = moveset_at(sp, lvl)
        if len(ms) < 2:
            continue
        best, ranked = C.best_move(ms, sp, enemy)
        if not best or ranked[0][1] == 0:
            continue
        e = C.mon(enemy)
        m = C.move(best)
        x = C.type_multiplier(m["type"], e["types"])
        stab = " with STAB" if m["type"] in C.mon(sp)["types"] else ""
        multtxt = {0.5: "not very effective (0.5x)", 1.0: "neutral (1x)",
                   2.0: "super effective (2x)", 4.0: "4x super effective"}.get(x, f"{x}x")
        facts = C.battle_facts(enemy, sp, ms)
        ehp = rng.randint(8, 40)
        ohpmax = 20 + lvl * 2
        ohp = rng.randint(int(ohpmax * 0.5), ohpmax)
        state = (f"In battle (wild). Our {sp} L{lvl} HP {ohp}/{ohpmax}, moves: {', '.join(ms)}. "
                 f"Enemy {enemy} L{elvl} HP {ehp}/{ehp}.")
        think = rng.choice(THINK_MOVE).format(
            enemy=enemy, etypes="/".join(e["types"]), best=best, mult=multtxt, stab=stab)
        act = json.dumps({"action": "fight", "move": best})
        ex = prompts.format_example(facts, state, "Win this battle efficiently.", think, act)
        ex["meta"] = {"kind": "battle_move", "label": best}
        out.append(ex)
    return out


def gen_catch_flee(n=600):
    """Run rule: only Pikachu is caught; weak wilds are fought or fled."""
    _, _, _, enc = C._db()
    out = []
    maps = [k for k, v in enc.items() if "grass" in v]
    pika_maps = [m for m in maps if any(r["species"] == "PIKACHU" for r in enc[m]["grass"]["mons"])]
    while len(out) < n:
        if pika_maps and rng.random() < 0.2:   # oversample the catch case
            mp = rng.choice(pika_maps)
            row = rng.choice([r for r in enc[mp]["grass"]["mons"] if r["species"] == "PIKACHU"])
        else:
            mp = rng.choice(maps)
            row = rng.choice(enc[mp]["grass"]["mons"])
        sp, lvl = row["species"], row["level"]
        facts = C.map_facts(mp)
        our = rng.choice([("CHARMELEON", 28, 60, 84), ("PIKACHU", 15, 30, 40)])
        state = (f"In battle (wild) on {mp}. Enemy {sp} L{lvl} at full HP. "
                 f"Our {our[0]} L{our[1]} HP {our[2]}/{our[3]}. Poké Balls: {rng.randint(1,9)}.")
        if sp == "PIKACHU":
            think = "GOAL says catch only Pikachu — this IS a wild Pikachu. Weaken then throw; HP is full so a ball now is the play per goal."
            act = json.dumps({"action": "throw_ball"})
            goal = "Catch a Pikachu. Do not catch anything else."
            kind = "catch"
        elif rng.random() < 0.5:
            think = f"{sp} is not Pikachu; the catch rule says skip it. It's weak — fight it for XP."
            best, _ = C.best_move(moveset_at(our[0], our[1]), our[0], sp)
            if not best:
                continue
            act = json.dumps({"action": "fight", "move": best})
            goal = "Level up. Catch only Pikachu, nothing else."
            kind = "fight_wild"
        else:
            think = f"{sp} is not Pikachu (catch rule) and we need to conserve HP/PP for the trip — flee."
            act = json.dumps({"action": "flee"})
            goal = "Reach the destination safely. Catch only Pikachu; avoid needless fights."
            kind = "flee"
        ex = prompts.format_example(facts, state, goal, think, act)
        ex["meta"] = {"kind": kind}
        out.append(ex)
    return out


def generate():
    return gen_move_choice() + gen_catch_flee()


if __name__ == "__main__":
    xs = generate()
    print(len(xs), "battle examples")
    print(json.dumps(xs[0], indent=1)[:900])
