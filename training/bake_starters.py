"""Bake three starting states — one per starter — from ck-lab-before-starter.

Each: grab the ball, decline nickname, walk toward the exit to trigger the
rival fight, battle it out (win or lose — both are valid starts), save state.
Outputs run/start-{charmander,squirtle,bulbasaur}.state
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import executor as X

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "run/ck-lab-before-starter.state"
BALLS = {"charmander": 6, "squirtle": 7, "bulbasaur": 8}
ATK = ("b:8 wait:110 left:4 wait:12 up:4 wait:12 a:8 wait:40 "
       "up:4 wait:12 up:4 wait:12 up:4 wait:12 a:8 wait:470 b:8 wait:160 b:8 wait:160")


def bake(name: str, bx: int) -> bool:
    out = ROOT / f"run/start-{name}.state"
    shutil.copy(SRC, out)
    emu = X.Emu(out)
    # the checkpoint is mid-Oak-dialog: press B until movement works again
    for _ in range(30):
        emu.run("b:8 wait:130")
        emu.run("down:16 wait:14")
        if emu.snapshot()["y"] != 3:
            break
    # walk to below the ball, face up, take it
    s = emu.snapshot()
    dx = bx - s["x"]
    mv = f"right:{16*dx}" if dx > 0 else f"left:{16*-dx}"
    emu.run(f"{mv} wait:12 up:4 wait:12")
    s = emu.snapshot()
    if (s["x"], s["y"]) != (bx, 4):
        print(f"  approach off: at {(s['x'], s['y'])} want {(bx, 4)}")
    emu.run("a:8 wait:200")                      # ball dialog
    emu.run("a:8 wait:200")                      # "So! You want X?" -> YES
    emu.run("b:8 wait:150 b:8 wait:150")         # decline nickname, close text
    s = emu.snapshot()
    if s["party_n"] != 1:
        # try once more with an extra confirm
        emu.run("a:8 wait:250 b:8 wait:150 b:8 wait:150")
        s = emu.snapshot()
        if s["party_n"] != 1:
            print(f"{name}: FAILED to obtain starter (party={s['party_n']})")
            return False
    got = s["party"][0]["species"]
    # walk toward the exit until the rival stops us
    for _ in range(14):
        s = emu.snapshot()
        if s["in_battle"]:
            break
        emu.run("down:16 wait:14 b:8 wait:60")
    # fight the rival with move slot 0
    for _ in range(20):
        s = emu.snapshot()
        if not s["in_battle"]:
            break
        emu.run(ATK)
    emu.run("b:8 wait:150 b:8 wait:120 b:8 wait:120")
    s = emu.snapshot()
    lead = s["party"][0]
    print(f"{name}: got {got}, after rival: L{lead['level']} {lead['hp']}/{lead['max_hp']} "
          f"map={s['map']} battle={s['in_battle']}")
    return True


if __name__ == "__main__":
    for name, bx in BALLS.items():
        bake(name, bx)
