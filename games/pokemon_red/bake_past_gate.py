"""Bake starter states PAST the Viridian 'You can't go through here!' gate.

The sleeping old man at Viridian (18,9) blocks the north exit until you have the
Pokédex, which requires Oak's Parcel errand: get the Parcel from the Viridian Poké
Mart clerk, deliver it to Prof. Oak in Pallet. This is tutorial busywork, identical
for every starter — we bake past it so the autonomous fleet can measure the actual
gym progression (Route 2 -> Viridian Forest -> Pewter -> Brock -> ... -> Misty).

Outputs run/gate-{name}.state. Usage: python3 bake_past_gate.py
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402
import executor as X
import navigate as NAV

ROOT = _bootstrap.REPO_ROOT
PALLET, ROUTE1, VIRIDIAN = 0, 12, 1
VIRIDIAN_MART, OAKS_LAB = None, 40   # mart id resolved below
for mid, e in NAV.registry().items():
    if e["const"] == "VIRIDIAN_MART":
        VIRIDIAN_MART = mid


def cross_north(emu, tries=3):
    for _ in range(tries):
        s = emu.snapshot()
        gg = NAV.grid(s["map"]); h = gg[2] if gg else 36
        emu.do({"action": "walk_to", "x": s["x"], "y": 0}, s)


def go(emu, target_map, edge, cap=8):
    """Walk toward a map edge to cross to target_map; returns True on arrival."""
    for _ in range(cap):
        s = emu.snapshot()
        if s["map"] == target_map:
            return True
        gg = NAV.grid(s["map"])
        if not gg:
            emu.run("b:8 wait:30"); continue
        _g, w, h = gg
        ex = {"north": (s["x"], 0), "south": (s["x"], h - 1),
              "west": (0, s["y"]), "east": (w - 1, s["y"])}[edge]
        emu.do({"action": "walk_to", "x": ex[0], "y": ex[1]}, s)
    return emu.snapshot()["map"] == target_map


def walk_door(emu, map_id, door, cap=12):
    for _ in range(cap):
        s = emu.snapshot()
        if s["map"] != map_id:
            return True
        if (s["x"], s["y"]) == door:
            # step onto/through the door (down for exits, or into it)
            emu.run("down:16 wait:30 up:16 wait:30")
            continue
        st = NAV.bfs_path(map_id, (s["x"], s["y"]), door)
        if st:
            emu.run(NAV.steps_to_script(st, cap=8))
        else:
            emu.run("b:8 wait:20")
    return emu.snapshot()["map"] != map_id


def errand(name):
    src = ROOT / f"run/start-{name}.state"
    out = ROOT / f"run/gate-{name}.state"
    shutil.copy(src, out)
    emu = X.Emu(out)
    s = emu.snapshot()
    print(f"[{name}] start map={s['map']} pos=({s['x']},{s['y']})", flush=True)

    # If a starter state got left mid-map, first get to Pallet or Viridian sanely.
    # --- Phase 1: reach Viridian, enter the Mart ---
    for _ in range(6):
        s = emu.snapshot()
        if s["map"] == VIRIDIAN:
            break
        if s["map"] == PALLET:
            go(emu, ROUTE1, "north"); continue
        if s["map"] == ROUTE1:
            go(emu, VIRIDIAN, "north"); continue
        emu.run("b:8 wait:30")
    print(f"[{name}] reached map={emu.snapshot()['map']} (want {VIRIDIAN})", flush=True)
    # to Mart door (29,19) and in
    for _ in range(10):
        s = emu.snapshot()
        if s["map"] == VIRIDIAN_MART:
            break
        st = NAV.bfs_path(VIRIDIAN, (s["x"], s["y"]), (29, 19))
        if st:
            emu.run(NAV.steps_to_script(st, cap=8))
        else:
            emu.run("b:8 wait:20")
        if (emu.snapshot()["x"], emu.snapshot()["y"]) == (29, 19):
            emu.run("up:16 wait:40")
    print(f"[{name}] in mart? map={emu.snapshot()['map']} (want {VIRIDIAN_MART})", flush=True)

    # --- Phase 2: talk to clerk (0,5), get parcel ---
    for _ in range(8):
        s = emu.snapshot()
        if s["map"] != VIRIDIAN_MART:
            break
        if (s["x"], s["y"]) == (1, 5):
            emu.run("left:4 wait:12 a:8 wait:150 a:8 wait:150 a:8 wait:150 b:8 wait:60")
            break
        st = NAV.bfs_path(VIRIDIAN_MART, (s["x"], s["y"]), (1, 5))
        emu.run(NAV.steps_to_script(st, cap=6) if st else "up:16 wait:16")
    print(f"[{name}] parcel talk done, bag has parcel-ish. exiting mart", flush=True)

    # --- Phase 3: exit mart -> Viridian -> Pallet -> Oak's lab ---
    for _ in range(8):
        s = emu.snapshot()
        if s["map"] == VIRIDIAN:
            break
        emu.run("down:16 wait:20")   # exit is at the bottom
    for _ in range(6):
        s = emu.snapshot()
        if s["map"] == PALLET:
            break
        if s["map"] == VIRIDIAN:
            go(emu, ROUTE1, "south"); continue
        if s["map"] == ROUTE1:
            go(emu, PALLET, "south"); continue
        emu.run("b:8 wait:30")
    print(f"[{name}] back at map={emu.snapshot()['map']} (want {PALLET})", flush=True)
    # into Oak's lab (door 12,11)
    for _ in range(10):
        s = emu.snapshot()
        if s["map"] == OAKS_LAB:
            break
        st = NAV.bfs_path(PALLET, (s["x"], s["y"]), (12, 11))
        emu.run(NAV.steps_to_script(st, cap=8) if st else "b:8 wait:20")
        if (emu.snapshot()["x"], emu.snapshot()["y"]) == (12, 11):
            emu.run("up:16 wait:40")

    # --- Phase 4: Oak Pokédex cutscene (walk up, advance a LOT of dialog) ---
    for _ in range(6):
        emu.run("up:16 wait:20")
    for _ in range(25):
        emu.run("a:8 wait:120")
    for _ in range(6):
        emu.run("b:8 wait:100 down:16 wait:16")   # clear + move to break free
    s = emu.snapshot()
    print(f"[{name}] after Oak: map={s['map']} pos=({s['x']},{s['y']})", flush=True)
    return out


def verify_gate_open(state):
    """Can we now cross north from Viridian? Get to Viridian and test the crossing."""
    emu = X.Emu(state)
    for _ in range(8):
        s = emu.snapshot()
        if s["map"] == VIRIDIAN:
            break
        if s["map"] == PALLET:
            go(emu, ROUTE1, "north")
        elif s["map"] == ROUTE1:
            go(emu, VIRIDIAN, "north")
        else:
            emu.run("b:8 wait:30")
    before = emu.snapshot()["map"]
    ok = go(emu, ROUTE1 if False else 13, "north")  # Viridian north -> Route2 (id 13)
    after = emu.snapshot()
    return after["map"] == 13, before, after


if __name__ == "__main__":
    for name in ["squirtle", "bulbasaur", "charmander"]:
        out = errand(name)
        opened, b, a = verify_gate_open(out)
        print(f"== {name}: gate_open={opened} (Viridian->Route2). "
              f"final map={a['map']} pos=({a['x']},{a['y']})\n", flush=True)
