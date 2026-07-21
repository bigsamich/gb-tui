"""Execute model actions against the emulator via the gb-agent CLI.

Ports the battle/menu macros proven during the manual playthrough. Each op runs
`gb-agent --state <file> --script ...` (loads state, runs, saves back).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context as C
import navigate as NAV

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "target/release/gb-agent"
ROM = ROOT / "test-roms/pokemon-red.gb"

# proven macros
FLEE = ("b:8 wait:140 b:8 wait:100 b:8 wait:30 up:4 wait:12 left:4 wait:12 "
        "down:4 wait:16 right:4 wait:16 a:8 wait:240 b:8 wait:120")
SWITCH = ("b:8 wait:120 left:4 wait:16 up:4 wait:16 right:4 wait:16 a:8 wait:60 "
          "down:4 wait:20 a:8 wait:60 a:8 wait:180")
BALL = ("b:8 wait:120 left:4 wait:14 up:4 wait:14 down:4 wait:14 a:8 wait:80 "
        "a:8 wait:200 b:8 wait:400 b:8 wait:200 b:8 wait:200")


def _attack_script(slot: int) -> str:
    downs = " ".join(["down:4 wait:12"] * slot)
    return ("b:8 wait:110 left:4 wait:12 up:4 wait:12 a:8 wait:40 "
            "up:4 wait:12 up:4 wait:12 up:4 wait:12 " + (downs + " " if downs else "") +
            "a:8 wait:470 b:8 wait:160 b:8 wait:160")


class Emu:
    def __init__(self, state_file: Path):
        self.state = Path(state_file)

    def run(self, script: str, peekhex=None) -> str:
        cmd = [str(AGENT), "--rom", str(ROM), "--state", str(self.state), "--script", script]
        if peekhex:
            cmd += ["--peekhex", peekhex]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return r.stdout

    def peekblock(self, addr: str, length: int) -> bytes:
        out = self.run("wait:2", peekhex=f"{addr}:{length}")
        m = re.search(r'hex@0x[0-9a-f]+:((?: [0-9a-f]{2})+)', out)
        return bytes.fromhex(m.group(1).replace(" ", "")) if m else b""

    # ---- state snapshot ----
    # Full id->name tables auto-built from the disassembly name lists:
    # internal species id = 1-based order in pokemon/names.asm;
    # move id = 1-based order in moves/names.asm.
    @staticmethod
    def _load_tables():
        def norm(s):
            return (s.replace("♂", "_M").replace("♀", "_F").replace("'", "")
                     .replace("-", "_").replace(".", "").replace(" ", "_").upper())
        sp = {}
        for i, m in enumerate(re.finditer(
                r'dname "([^"]+)"', (ROOT / "assets/gamedata/pokemon/names.asm").read_text())):
            sp[i + 1] = norm(m.group(1))
        mv = {}
        for i, m in enumerate(re.finditer(
                r'li "([^"]+)"', (ROOT / "assets/gamedata/moves/names.asm").read_text())):
            mv[i + 1] = norm(m.group(1))
        return sp, mv

    SPECIES, MOVES = None, None

    def species_name(self, sid: int) -> str:
        if Emu.SPECIES is None:
            Emu.SPECIES, Emu.MOVES = Emu._load_tables()
        return Emu.SPECIES.get(sid, f"SPECIES_{sid:02X}")

    def snapshot(self) -> dict:
        d = self.peekblock("D000", 1024)   # D000..D3FF
        cf = self.peekblock("CF00", 256)   # CF00..CFFF (enemy battle data)
        cc = self.peekblock("CC00", 256)
        def db(a): return d[a - 0xD000]
        def dw(a): return (d[a - 0xD000] << 8) | d[a - 0xD000 + 1]
        s = {
            "map": db(0xD35E), "x": db(0xD362), "y": db(0xD361),
            "badges": db(0xD356), "party_n": db(0xD163),
            "in_battle": db(0xD057), "money": int(f"{db(0xD347):02x}{db(0xD348):02x}{db(0xD349):02x}"),
            "active_idx": cc[0x2F],
        }
        if Emu.SPECIES is None:
            Emu.SPECIES, Emu.MOVES = Emu._load_tables()
        mons = []
        for i in range(s["party_n"]):
            base = 0xD16B + 44 * i
            moves = [Emu.MOVES.get(db(base + 8 + j), f"M{db(base+8+j)}")
                     for j in range(4) if db(base + 8 + j)]
            pp = [db(base + 0x1D + j) for j in range(4)]
            mons.append({"species": self.species_name(db(0xD164 + i)),
                         "level": db(base + 0x21), "hp": dw(base + 1), "max_hp": dw(base + 0x22),
                         "moves": moves, "pp": pp[:len(moves)]})
        s["party"] = mons
        # bag: D31D count, D31E onwards (id, qty) pairs
        bag = {}
        n_items = db(0xD31D)
        for i in range(min(n_items, 20)):
            iid, qty = db(0xD31E + 2 * i), db(0xD31F + 2 * i)
            if iid in (0, 0xFF):
                break
            bag[iid] = qty
        s["bag"] = bag
        s["balls"] = sum(q for iid, q in bag.items() if iid in (1, 2, 3, 4))
        if s["in_battle"]:
            s["enemy_species"] = self.species_name(cf[0xE5])
            s["enemy_hp"] = (cf[0xE6] << 8) | cf[0xE7]
            s["enemy_level"] = cf[0xF3]
        return s

    # ---- action execution ----
    def do(self, act: dict, snap: dict) -> str:
        a = act.get("action")
        if a == "walk_to":
            # dismiss any lingering NPC/text box first — an open dialog blocks ALL
            # movement (e.g. the clerk's "say hi to Prof. Oak!" after giving the parcel),
            # and the model otherwise gets wedged issuing walk_to's that can't fire.
            self.run("b:8 wait:16 b:8 wait:16")
            snap = self.snapshot()
            mx, my = snap["x"], snap["y"]
            tx, ty = int(act["x"]), int(act["y"])
            # INTERIOR EXIT: leaving a building is a door WARP (to LAST_MAP), not an
            # overworld edge. If the target is a building exit, walk onto it and step
            # THROUGH (arriving on the tile isn't leaving — you must step off the mat).
            mapn = NAV.registry().get(snap["map"], {}).get("name", "")
            exits = [(x, y) for x, y, lbl in C.map_warps(mapn) if lbl.startswith("Exit")]
            if (tx, ty) in exits:
                # walk FULLY onto the exit tile (steps_to_script only does one segment)
                for _ in range(8):
                    s2 = self.snapshot()
                    if (s2["x"], s2["y"]) == (tx, ty):
                        break
                    st = NAV.bfs_path(snap["map"], (s2["x"], s2["y"]), (tx, ty))
                    if not st:
                        break
                    self.run(NAV.steps_to_script(st, cap=10))
                # now step THROUGH the door (down is standard; try others if needed)
                for d in ("down", "left", "right", "up"):
                    before = self.snapshot()["map"]
                    self.run(f"{d}:16 wait:30")
                    if self.snapshot()["map"] != before:
                        return "exited-building"
                return "exit-try"
            steps = NAV.bfs_path(snap["map"], (mx, my), (tx, ty))
            if steps:
                self.run(NAV.steps_to_script(steps))
                return "walked"
            # no intra-map path: if the target is at/over a map edge that CONNECTS to
            # another map, walk to that edge and step off (overworld progression).
            gg = NAV.grid(snap["map"])
            if gg:
                _g, w, h = gg
                for cond, d in ((ty <= 1, "north"), (ty >= h - 2, "south"),
                                (tx <= 1, "west"), (tx >= w - 2, "east")):
                    if cond:
                        sc = NAV.cross_edge_script(snap["map"], (mx, my), d)
                        if sc:
                            self.run(sc)
                            return f"cross-{d}"
            return "no-path"
        if a == "fight":
            active = snap["party"][snap.get("active_idx", 0)] if snap["party"] else None
            slot = 0
            if active and act.get("move") in active["moves"]:
                slot = active["moves"].index(act["move"])
            self.run(_attack_script(slot))
            return f"attacked slot {slot}"
        if a == "flee":
            self.run(FLEE)
            return "fled"
        if a == "switch":
            self.run(SWITCH)
            return "switched"
        if a == "throw_ball":
            self.run(BALL)
            return "threw ball"
        if a == "press":
            self.run(str(act.get("buttons", "b:8 wait:60")))
            return "pressed"
        if a == "interact":
            self.run("a:8 wait:180 b:8 wait:60")
            return "interacted"
        if a == "heal_at_center":
            # COMPLETE heal at ANY Pokémon Center: if in a city, walk to the PC door
            # and enter; once inside, go to the nurse (counter at (3,3), nurse (3,1))
            # and heal. All PC interiors share this layout (map name has 'Pokecenter').
            def _is_pc(mid):
                return "Pokecenter" in NAV.registry().get(mid, {}).get("name", "")
            for _ in range(8):
                self.run("b:8 wait:16")            # dismiss any dialog
                s2 = self.snapshot()
                m = s2["map"]
                if _is_pc(m):
                    st = NAV.bfs_path(m, (s2["x"], s2["y"]), (3, 3))
                    if st:
                        self.run(NAV.steps_to_script(st, cap=10))
                    self.run("up:8 wait:16 a:8 wait:130 a:8 wait:150 a:8 wait:560 "
                             "a:8 wait:150 a:8 wait:150 b:8 wait:90")   # nurse heal dialog
                    if all(p["hp"] == p["max_hp"] for p in self.snapshot()["party"]):
                        return "healed"
                else:
                    mapn = NAV.registry().get(m, {}).get("name", "")
                    pcs = [(x, y) for x, y, lbl in C.map_warps(mapn) if "Center" in lbl]
                    if not pcs:
                        return "no-center-here"
                    door = pcs[0]
                    s3 = self.snapshot()
                    if (s3["x"], s3["y"]) == door:
                        self.run("up:16 wait:30")          # step through the door
                    else:
                        st = NAV.bfs_path(m, (s3["x"], s3["y"]), door)
                        if st:
                            self.run(NAV.steps_to_script(st, cap=10))
                        else:
                            return "no-path-to-center"
            return "heal-incomplete"
        if a == "done":
            return "done"
        return f"unknown-action {a}"


def state_text(s: dict) -> str:
    e = NAV.registry().get(s["map"], {})
    mapn = e.get("name", f"map{s['map']}")
    if s["in_battle"]:
        kind = "wild" if s["in_battle"] == 1 else "trainer"
        bits = [f"In battle ({kind}). Enemy {s.get('enemy_species')} L{s.get('enemy_level')} "
                f"HP {s.get('enemy_hp')}."]
        act = s["party"][s.get("active_idx", 0)] if s["party"] else None
        if act:
            bits.append(f"Active: {act['species']} L{act['level']} HP {act['hp']}/{act['max_hp']}, "
                        f"moves: {', '.join(f'{m}({p})' for m, p in zip(act['moves'], act['pp']))}.")
    else:
        bits = [f"Overworld on map {mapn} at ({s['x']},{s['y']})."]
    for p in s["party"]:
        bits.append(f"{p['species']} L{p['level']} HP {p['hp']}/{p['max_hp']} "
                    f"[{'/'.join(p['moves'])}]")
    bits.append(f"Money ${s['money']}. Badges {bin(s['badges']).count('1')}.")
    held = C.bag_text(s.get("bag", {}))    # the model can see its bag / quest items
    if held:
        bits.append(held)
    return " ".join(bits)


def ctx_for(s: dict) -> dict:
    e = NAV.registry().get(s["map"], {})
    ctx = {"map_name": e.get("name", ""), "party": [
        {"species": p["species"], "level": p["level"]} for p in s["party"][:2]]}
    if s["in_battle"]:
        act = s["party"][s.get("active_idx", 0)] if s["party"] else None
        ctx.update(in_battle=True, enemy_species=s.get("enemy_species", ""),
                   our_species=act["species"] if act else "",
                   our_moves=act["moves"] if act else [])
    return ctx


if __name__ == "__main__":
    import shutil, tempfile
    src = ROOT / "run/ck-cerulean-pika-L11-healed.state"
    tmp = Path(tempfile.mkdtemp()) / "t.state"
    shutil.copy(src, tmp)
    emu = Emu(tmp)
    s = emu.snapshot()
    print(json.dumps({k: v for k, v in s.items() if k != "party"}, indent=1))
    for p in s["party"]:
        print(" ", p)
    print(state_text(s))
