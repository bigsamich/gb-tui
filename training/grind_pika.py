"""Scripted Pikachu grind to L20 on Route 4 grass (CPU-only; runs alongside training).

Rules (from the user):
  - Pikachu leads. It only fights clearly-safe wilds (full XP); otherwise switch
    to Charmeleon immediately (CC2F-verified switch).
  - Never black out: heal at the Cerulean PC whenever HP gets low.
Progress prints one line per event; state is the REAL playthrough (run/bobby.state).
"""

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context as C
import executor as X
import navigate as NAV

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "run/bobby.state"

GRASS = (70, 12)          # Route 4 grass anchor
CITY_TO_R4 = (1, 18)      # Cerulean west edge
R4_TO_CITY = (88, 10)     # Route 4 east edge
PC_DOOR = (19, 17)
MAP = {"route4": 15, "cerulean": 3, "pc": 64, "mart": 67}


def exits_of(map_name):
    f = ROOT / "assets/gamedata/maps/objects" / f"{map_name}.asm"
    return [(int(m.group(1)), int(m.group(2)))
            for m in re.finditer(r'warp_event\s+(\d+),\s*(\d+),\s*LAST_MAP', f.read_text())]


def walk_toward(emu, s, target, blocked=frozenset()):
    steps = NAV.bfs_path(s["map"], (s["x"], s["y"]), target, blocked)
    if not steps:
        return False
    emu.run(NAV.steps_to_script(steps, cap=8))
    return True


def pika(s):
    for p in s["party"]:
        if p["species"] == "PIKACHU":
            return p
    return None


def char(s):
    for p in s["party"]:
        if p["species"] == "CHARMELEON":
            return p
    return None


def cc2f(emu):
    b = emu.peekblock("CC2F", 1)
    return b[0] if b else 0


def handle_battle(emu, s):
    pk, ch = pika(s), char(s)
    active = s["party"][s.get("active_idx", 0)] if s["party"] else None
    enemy, elvl = s.get("enemy_species", "?"), s.get("enemy_level", 99)
    pika_active = active and active["species"] == "PIKACHU"
    e = C.mon(enemy)
    immune = e and C.type_multiplier("ELECTRIC", e["types"]) == 0.0
    safe = (elvl <= pk["level"] + 1 and pk["hp"] > pk["max_hp"] * 0.45
            and not immune and s["in_battle"] == 1)
    if pika_active and not safe:
        # switch to Charmeleon, verify via CC2F, retry
        for _ in range(4):
            if cc2f(emu) == 1:
                break
            emu.run(X.SWITCH)
        print(f"  switch->CHARMELEON vs {enemy} L{elvl}", flush=True)
        return
    fighter = active["species"] if active else "CHARMELEON"
    moves = active["moves"] if active else []
    best, _ = C.best_move(moves, fighter, enemy)
    slot = moves.index(best) if best in moves else 0
    emu.run(X._attack_script(slot))
    print(f"  {fighter} uses {best or moves[0] if moves else '?'} vs {enemy} L{elvl}", flush=True)


def journey(emu, s, where):
    """One movement tick toward a destination across maps."""
    m = s["map"]
    if where == "grass":
        if m == MAP["route4"]:
            if abs(s["x"] - GRASS[0]) <= 2 and abs(s["y"] - GRASS[1]) <= 2:
                return "there"
            return walk_toward(emu, s, GRASS) or bump(emu)
        if m == MAP["cerulean"]:
            if s["x"] <= CITY_TO_R4[0] + 1:
                emu.run("left:48 wait:30")
                return True
            return walk_toward(emu, s, CITY_TO_R4) or bump(emu)
        if m == MAP["pc"]:
            emu.run("b:8 wait:90")
            emu.run("down:2 wait:20")
            emu.run("down:16 wait:20 down:16 wait:20 down:16 wait:20 down:16 wait:30")
            return True
        if m == MAP["mart"]:
            ex = exits_of("CeruleanMart")
            if ex and walk_toward(emu, s, ex[0]):
                return True
            emu.run("down:16 wait:20")
            return True
    if where == "pc":
        if m == MAP["pc"]:
            return "there"
        if m == MAP["route4"]:
            if s["x"] >= R4_TO_CITY[0] - 1:
                emu.run("right:32 wait:30")
                return True
            return walk_toward(emu, s, R4_TO_CITY) or bump(emu)
        if m == MAP["cerulean"]:
            if (s["x"], s["y"]) == (PC_DOOR[0], PC_DOOR[1] + 1):
                emu.run("up:16 wait:40")
                return True
            return walk_toward(emu, s, (PC_DOOR[0], PC_DOOR[1] + 1)) or bump(emu)
    emu.run("b:8 wait:60")
    return True


def bump(emu):
    emu.run("a:8 wait:120 b:8 wait:60")   # NPC/dialog unstick
    return True


def heal(emu, s):
    if (s["x"], s["y"]) != (3, 3):
        if not walk_toward(emu, s, (3, 4)):
            bump(emu)
            return False
        emu.run("up:16 wait:16")
    emu.run("a:8 wait:130 a:8 wait:150 a:8 wait:560 a:8 wait:150 a:8 wait:150 b:8 wait:90")
    s2 = X.Emu(STATE).snapshot()
    ok = all(p["hp"] == p["max_hp"] for p in s2["party"])
    print(f"  heal attempt -> {'OK' if ok else 'retry'}", flush=True)
    return ok


def main():
    emu = X.Emu(STATE)
    mode = "grind"
    last_lvl = 0
    for tick in range(4000):
        s = emu.snapshot()
        pk, ch = pika(s), char(s)
        if not pk:
            print("no pikachu?!", flush=True)
            return
        if pk["level"] != last_lvl:
            print(f"== PIKACHU L{pk['level']} (hp {pk['hp']}/{pk['max_hp']}) "
                  f"char L{ch['level']} hp {ch['hp']} money ${s['money']}", flush=True)
            last_lvl = pk["level"]
        if pk["level"] >= 20:
            print("== TARGET REACHED: PIKACHU L20 ==", flush=True)
            return
        if s["in_battle"]:
            handle_battle(emu, s)
            continue
        need_heal = pk["hp"] < pk["max_hp"] * 0.35 or (ch and ch["hp"] < 30)
        mode = "pc" if need_heal else "grind"
        if mode == "pc":
            r = journey(emu, s, "pc")
            if r == "there":
                heal(emu, s)
            continue
        r = journey(emu, s, "grass")
        if r == "there":
            # pace in the grass to trigger encounters
            emu.run("left:16 wait:8" if tick % 2 else "right:16 wait:8")
    print("tick cap reached", flush=True)


if __name__ == "__main__":
    main()
