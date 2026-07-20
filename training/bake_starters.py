"""Bake three starting states — one per starter — from ck-lab-before-starter.

Verified sequence (screenshot-checked): clear Oak's intro -> approach ball ->
A x4 (2 dex pages + "you want" text + YES/NO menu) -> A (YES) -> B x2 (decline
nickname) -> walk down to trigger the rival -> battle -> clear Oak's tutorial
until free to move. Outputs run/start-{name}.state.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import executor as X

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "run/ck-lab-before-starter.state"
BALL_X = {"charmander": 6, "squirtle": 7, "bulbasaur": 8}
ATK = ("b:8 wait:110 left:4 wait:12 up:4 wait:12 a:8 wait:40 "
       "up:4 wait:12 up:4 wait:12 up:4 wait:12 a:8 wait:470 b:8 wait:160 b:8 wait:160")


def clear_until_free(emu, tries=30) -> bool:
    for _ in range(tries):
        b = emu.snapshot()
        if b["in_battle"]:
            return True
        emu.run("b:8 wait:110")
        emu.run("down:16 wait:14")
        a = emu.snapshot()
        if (a["x"], a["y"]) != (b["x"], b["y"]) or a["map"] != b["map"]:
            return True
    return False


def bake(name: str, bx: int) -> bool:
    out = ROOT / f"run/start-{name}.state"
    shutil.copy(SRC, out)
    emu = X.Emu(out)
    if not clear_until_free(emu):
        print(f"{name}: intro never cleared"); return False
    # approach ball from below (row 4), face up
    s = emu.snapshot()
    dx = bx - s["x"]
    mv = f"right:{16*dx}" if dx > 0 else (f"left:{16*-dx}" if dx < 0 else "wait:2")
    emu.run(f"{mv} wait:12 up:4 wait:12")
    # dex (2 pages) + "you want" text + YES/NO menu, then confirm YES
    emu.run("a:8 wait:200 a:8 wait:160 a:8 wait:160 a:8 wait:160 a:8 wait:230")
    emu.run("b:8 wait:180 b:8 wait:180")           # decline nickname
    s = emu.snapshot()
    if s["party_n"] != 1:
        print(f"{name}: FAILED to obtain (party={s['party_n']})"); return False
    got = s["party"][0]["species"]
    # walk toward the exit until the rival stops us
    for _ in range(16):
        s = emu.snapshot()
        if s["in_battle"]:
            break
        emu.run("down:16 wait:14 b:8 wait:60")
    # fight the rival with move slot 0
    for _ in range(20):
        if not emu.snapshot()["in_battle"]:
            break
        emu.run(ATK)
    # clear Oak's post-battle tutorial until free to move
    clear_until_free(emu, tries=45)
    # walk OUT of the lab to Pallet Town, past Oak (avoids workers cornering on him)
    import navigate as NAV
    exited = False
    for _ in range(30):
        s = emu.snapshot()
        if s["map"] != 40:                    # left the lab
            exited = True
            break
        # head to the exit door at (4/5, 11), then step down through it
        steps = NAV.bfs_path(40, (s["x"], s["y"]), (5, 11)) or \
                NAV.bfs_path(40, (s["x"], s["y"]), (4, 11))
        if steps:
            emu.run(NAV.steps_to_script(steps, cap=10))
            emu.run("down:16 wait:30")
        else:
            emu.run("down:16 wait:14 b:8 wait:40")
    s = emu.snapshot()
    lead = s["party"][0]
    print(f"{name}: got {got} L{lead['level']}; exited-lab={exited} -> "
          f"map={s['map']} pos=({s['x']},{s['y']})")
    return exited and s["party_n"] == 1


if __name__ == "__main__":
    for name, bx in BALL_X.items():
        bake(name, bx)
