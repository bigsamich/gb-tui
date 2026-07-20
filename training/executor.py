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
    SPECIES = {0x54: "PIKACHU", 0xB0: "CHARMANDER", 0xB2: "CHARMELEON", 0xB4: "CHARIZARD"}
    MOVES = {1: "POUND", 10: "SCRATCH", 33: "TACKLE", 45: "GROWL", 52: "EMBER", 43: "LEER",
             84: "THUNDERSHOCK", 86: "THUNDER_WAVE", 98: "QUICK_ATTACK", 99: "RAGE",
             129: "SWIFT", 53: "FLAMETHROWER", 163: "SLASH"}

    def species_name(self, sid: int) -> str:
        if sid in self.SPECIES:
            return self.SPECIES[sid]
        try:  # fall back to dex order name via context db (internal ids differ; best effort)
            return f"SPECIES_{sid:02X}"
        except Exception:
            return f"SPECIES_{sid:02X}"

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
        mons = []
        for i in range(s["party_n"]):
            base = 0xD16B + 44 * i
            moves = [self.MOVES.get(db(base + 8 + j), f"M{db(base+8+j)}")
                     for j in range(4) if db(base + 8 + j)]
            pp = [db(base + 0x1D + j) for j in range(4)]
            mons.append({"species": self.species_name(db(0xD164 + i)),
                         "level": db(base + 0x21), "hp": dw(base + 1), "max_hp": dw(base + 0x22),
                         "moves": moves, "pp": pp[:len(moves)]})
        s["party"] = mons
        if s["in_battle"]:
            s["enemy_species"] = self.species_name(cf[0xE5])
            s["enemy_hp"] = (cf[0xE6] << 8) | cf[0xE7]
            s["enemy_level"] = cf[0xF3]
        return s

    # ---- action execution ----
    def do(self, act: dict, snap: dict) -> str:
        a = act.get("action")
        if a == "walk_to":
            steps = NAV.bfs_path(snap["map"], (snap["x"], snap["y"]),
                                 (int(act["x"]), int(act["y"])))
            if not steps:
                return "no-path"
            script = NAV.steps_to_script(steps)
            self.run(script)
            return "walked"
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
            # supported at Cerulean PC when on map 3 or 64
            if snap["map"] == 64:
                self.run("up:64 wait:16 a:8 wait:130 a:8 wait:150 a:8 wait:560 "
                         "a:8 wait:150 a:8 wait:150 b:8 wait:90")
                return "healed"
            if snap["map"] == 3:
                steps = NAV.bfs_path(3, (snap["x"], snap["y"]), (19, 18))
                if steps:
                    self.run(NAV.steps_to_script(steps, cap=10))
                    return "walking-to-pc"
                return "no-path"
            return "no-center-known"
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
