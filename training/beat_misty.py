"""Drive Bobby (run/bobby.state) to heal and beat Misty for the Cascade Badge.

Legs are hardcoded from the known playthrough route:
  Route 4 (15) -> Cerulean (3) -> PC (64) heal -> Cerulean -> Gym (65) -> Misty.
Battle: lead Pikachu (ThunderShock = 2x on Water); switch to Charmeleon if low.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context as C
import executor as X
import navigate as NAV

STATE = Path(__file__).resolve().parent.parent / "run/bobby.state"
emu = X.Emu(STATE)


def snap():
    return emu.snapshot()


def walk_to(map_id, target, blocked=frozenset(), cap=8):
    s = snap()
    if s["map"] != map_id:
        return False
    steps = NAV.bfs_path(map_id, (s["x"], s["y"]), target, blocked)
    if not steps:
        return None
    emu.run(NAV.steps_to_script(steps, cap=cap))
    return True


def clear_dialog():
    emu.run("b:8 wait:60 b:8 wait:40")


def heal_at_cerulean_pc():
    # to PC counter (3,3), full heal in ONE call, exit
    for _ in range(15):
        s = snap()
        if s["map"] != 64:
            walk_to(3, (19, 18)) or emu.run("b:8 wait:40")
            s2 = snap()
            if s2["map"] == 3 and (s2["x"], s2["y"]) == (19, 18):
                emu.run("up:16 wait:40")
            continue
        break
    # inside PC: go to counter and heal
    walk_to(64, (3, 4))
    emu.run("up:16 wait:16 a:8 wait:130 a:8 wait:150 a:8 wait:560 "
            "a:8 wait:150 a:8 wait:150 b:8 wait:90")
    s = snap()
    healed = all(p["hp"] == p["max_hp"] for p in s["party"])
    # exit PC
    emu.run("b:8 wait:90 down:2 wait:20 down:16 wait:20 down:16 wait:20 down:16 wait:30")
    return healed


def to_cerulean():
    for _ in range(25):
        s = snap()
        if s["map"] == 3:
            return True
        if s["map"] == 15:              # Route 4: east to Cerulean
            if s["x"] >= 87:
                emu.run("right:32 wait:30")
            else:
                if walk_to(15, (88, 10)) is None:
                    emu.run("up:16 wait:10")
        else:
            emu.run("b:8 wait:40")
    return snap()["map"] == 3


def battle_misty():
    ATK_TS = X._attack_script(0)          # Pikachu ThunderShock = slot 0
    for _ in range(30):
        s = snap()
        if s["badges"] & 2:
            return True
        if not s["in_battle"]:
            return s["badges"] & 2
        active = s["party"][s.get("active_idx", 0)]
        # protect Pikachu: if it's low and Charmeleon healthy, switch
        if active["species"] == "PIKACHU" and active["hp"] < active["max_hp"] * 0.3:
            ch = next((p for p in s["party"] if p["species"] == "CHARMELEON" and p["hp"] > 20), None)
            if ch:
                emu.run(X.SWITCH)
                continue
        if active["species"] == "PIKACHU":
            emu.run(ATK_TS)               # ThunderShock 2x on Water
        else:
            best, _ = C.best_move(active["moves"], active["species"], s.get("enemy_species", ""))
            slot = active["moves"].index(best) if best in active["moves"] else 0
            emu.run(X._attack_script(slot))
    return snap()["badges"] & 2


def phase(name):
    print(f"== {name} @ {time.strftime('%H:%M:%S')}", flush=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", choices=["heal", "gym", "all"])
    args = ap.parse_args()

    if args.phase in ("heal", "all"):
        phase("HEAL: travel to Cerulean")
        to_cerulean()
        phase("HEAL: at PC")
        ok = heal_at_cerulean_pc()
        s = snap()
        print("healed:", ok, "party:",
              [(p["species"], p["level"], f"{p['hp']}/{p['max_hp']}") for p in s["party"]], flush=True)

    if args.phase in ("gym", "all"):
        phase("GYM: to Cerulean gym door (30,19)")
        for _ in range(20):
            s = snap()
            if s["map"] == 65:
                break
            if s["map"] == 3:
                if (s["x"], s["y"]) == (30, 20):
                    emu.run("up:16 wait:40")
                elif walk_to(3, (30, 20)) is None:
                    emu.run("b:8 wait:40")
            else:
                emu.run("b:8 wait:40")
        phase("GYM: fight to Misty (4,2)")
        for _ in range(40):
            s = snap()
            if s["badges"] & 2:
                break
            if s["in_battle"]:
                battle_misty()
                continue
            if s["map"] != 65:
                break
            # walk toward Misty; trainers trigger on the way
            r = walk_to(65, (4, 3))
            if r is None:
                emu.run("up:16 wait:14 a:8 wait:150 b:8 wait:60")
            elif (s["x"], s["y"]) in ((4, 3), (5, 3), (4, 4)):
                emu.run("up:8 wait:16 a:8 wait:200")   # face/talk Misty
        s = snap()
        print("FINAL badges:", bin(s["badges"]), "party:",
              [(p["species"], p["level"], f"{p['hp']}/{p['max_hp']}") for p in s["party"]], flush=True)
