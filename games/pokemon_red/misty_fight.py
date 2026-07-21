"""Clean Misty driver from the healed checkpoint.

Strategy (learned the hard way):
  - Charmeleon TANKS the Jr Trainer (Cool Trainer F: Shellder + Goldeen) using
    EMBER (slot 2) — never RAGE (slot 0), whose lock-in desyncs the menu macro
    and freezes the loop. This preserves Pikachu at full HP.
  - Pikachu (full 44 HP) then SWEEPS Misty (Staryu L18, Starmie L21) with
    ThunderShock (slot 0), 2x super-effective on both.
Switching is index-aware and CC2F-verified with retries.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402
import context as C
import executor as X

ROOT = _bootstrap.REPO_ROOT
STATE = ROOT / "run/bobby.state"

PIKACHU, CHARMELEON = 0, 1        # party indices (Pikachu leads after grind)
TS = X._attack_script(0)          # Pikachu ThunderShock
EMBER = X._attack_script(2)       # Charmeleon Ember (NOT Rage)
WATER_JR = {"SHELLDER", "GOLDEEN", "HORSEA", "SEEL", "KRABBY", "POLIWAG"}
MISTY_MONS = {"STARYU", "STARMIE"}


def cc2f(emu):
    b = emu.peekblock("CC2F", 1)
    return b[0] if b else -1


def switch_to(emu, idx):
    """Switch active battler to party index idx; CC2F-verified, retried.
    Leading B-spam clears any battle-intro text (e.g. Misty's) so the PKMN-menu
    navigation lands on the real battle menu instead of a text box."""
    # The party menu cursor starts on the ACTIVE mon, not idx0 — so force it to the
    # top (up,up) then step DOWN to the target index. (down×0 alone would re-select
    # the active mon = "already in battle" = no switch.)
    downs = "down:4 wait:16 " * idx
    macro = ("b:8 wait:70 b:8 wait:70 b:8 wait:70 b:8 wait:70 b:8 wait:70 "  # fully clear text
             "left:4 wait:16 up:4 wait:16 right:4 wait:16 a:8 wait:90 "
             "up:4 wait:16 up:4 wait:16 "                           # force cursor to top
             + downs + "a:8 wait:70 a:8 wait:200")
    for _ in range(5):
        if cc2f(emu) == idx:
            return True
        emu.run(macro)
    return cc2f(emu) == idx


def do_turn(emu, slot):
    """Self-verifying attack: flush text -> select FIGHT+move -> advance until a
    turn actually resolves (HP changes / battle ends). Retries if the selection
    didn't land (the blind fixed-wait macro whiffed ~2/3 of the time)."""
    s0 = emu.snapshot()
    if not s0["in_battle"]:
        return
    ai0 = s0.get("active_idx", 0)
    en0 = s0.get("enemy_hp") or 0
    downs = "down:4 wait:14 " * slot
    for _ in range(5):
        # clear any pending text so we land on the battle menu
        emu.run("b:8 wait:90 b:8 wait:90 b:8 wait:90")
        # FIGHT (bottom-left) then move slot
        emu.run("left:4 wait:14 up:4 wait:14 a:8 wait:60 "
                "up:4 wait:14 up:4 wait:14 up:4 wait:14 " + downs + "a:8 wait:80")
        # advance until MY attack actually lands (enemy hp drops) or the fight
        # changes state. Do NOT exit on my-hp-drop alone: a faster enemy hitting
        # me first must not be mistaken for my turn resolving (that's the whiff).
        for _ in range(11):
            emu.run("b:8 wait:110")
            s = emu.snapshot()
            if not s["in_battle"]:
                return
            ai = s.get("active_idx", 0)
            myh = s["party"][ai]["hp"] if ai < len(s["party"]) else 0
            eh = s.get("enemy_hp") or 0
            if myh == 0:                 # I fainted -> caller handles replacement
                return
            if eh < en0 or eh > en0 + 5 or ai != ai0:  # my hit landed / new enemy
                return


POTION_ID = 20


def use_potion(emu, active_idx):
    """Use a Potion (id 20) on the active mon from the in-battle ITEM menu.
    Verified by HP going up; retried. Battle menu layout: FIGHT/PKMN (top),
    ITEM/RUN (bottom) -> ITEM is left+down. Selecting a heal item opens the
    party list directly (no USE/TOSS submenu in battle)."""
    s0 = emu.snapshot()
    if not s0["in_battle"] or s0["bag"].get(POTION_ID, 0) <= 0:
        return False
    ids = list(s0["bag"].keys())
    if POTION_ID not in ids:
        return False
    bag_idx = ids.index(POTION_ID)
    hp0 = s0["party"][active_idx]["hp"]
    for _ in range(4):
        # Redundant presses FORCE the cursor to FIGHT (top-left) from ANY position
        # (up,up -> top row; left,left -> left col), then down -> ITEM (bottom-left).
        # Single presses can drop on timing; doubling guarantees the position so it
        # never accidentally opens FIGHT or PKMN (a switch).
        emu.run("b:8 wait:70 b:8 wait:70 b:8 wait:70 b:8 wait:70 b:8 wait:70 "  # fully clear text
                "up:4 wait:16 up:4 wait:16 left:4 wait:16 left:4 wait:16 "
                "down:4 wait:16 a:8 wait:90 "                     # ITEM
                + "down:4 wait:14 " * bag_idx + "a:8 wait:90 "    # to POTION, select
                "up:4 wait:16 up:4 wait:16 "                      # force party cursor to top
                + "down:4 wait:16 " * active_idx + "a:8 wait:140 " # heal target mon
                "b:8 wait:70 b:8 wait:70")                        # clear "recovered" text
        s = emu.snapshot()
        if not s["in_battle"] or s["party"][active_idx]["hp"] > hp0:
            return True
    return False


def pick_replacement(emu):
    """A fainted mon forces the party menu; send out the first living mon.
    Tolerant of the double-faint transition: when my mon AND the enemy faint the
    same turn, a snapshot can momentarily read every HP as 0 — that is NOT a wipe,
    so advance a frame and recheck instead of giving up."""
    for _ in range(8):
        s = emu.snapshot()
        if not s["in_battle"]:
            return True
        ai = s.get("active_idx", 0)
        if ai < len(s["party"]) and s["party"][ai]["hp"] > 0:
            return True                    # a living mon is out
        alive = [i for i, p in enumerate(s["party"]) if p["hp"] > 0]
        if not alive:
            emu.run("b:8 wait:120")        # transient double-faint read -> advance & recheck
            continue
        emu.run("b:8 wait:100 b:8 wait:100 "           # clear "X fainted!" text
                "up:4 wait:16 up:4 wait:16 "            # force cursor to top (idx0)
                + "down:4 wait:16 " * alive[0]          # to first living mon
                + "a:8 wait:70 a:8 wait:160")           # select + send out
    s = emu.snapshot()
    return (not s["in_battle"]) or any(p["hp"] > 0 for p in s["party"])


def run():
    emu = X.Emu(STATE)
    s = emu.snapshot()
    print("start:", s["map"], (s["x"], s["y"]), "badges", bin(s["badges"]))

    # ---- exit Cerulean PC (map 64) to the street ----
    for _ in range(12):
        s = emu.snapshot()
        if s["map"] != 64:
            break
        emu.run("b:8 wait:60 down:16 wait:16")
    # ---- walk to gym door and enter (gym is map 65) ----
    import navigate as NAV
    for _ in range(30):
        s = emu.snapshot()
        if s["map"] == 65:
            break
        if s["map"] == 3:                       # Cerulean City
            if (s["x"], s["y"]) == (30, 20):
                emu.run("up:16 wait:40")
            else:
                st = NAV.bfs_path(3, (s["x"], s["y"]), (30, 20))
                emu.run(NAV.steps_to_script(st, cap=12) if st else "down:16 wait:16")
        else:
            emu.run("b:8 wait:40")
    s = emu.snapshot()
    print("at gym entry:", s["map"], (s["x"], s["y"]))

    # ---- gym loop: single-step BFS toward the tile right of Misty (5,2) ----
    DIRS = {"u": "up:16 wait:14", "d": "down:16 wait:14",
            "l": "left:16 wait:14", "r": "right:16 wait:14"}
    for step in range(160):
        s = emu.snapshot()
        if s["badges"] & 2:
            print("*** CASCADE BADGE — MISTY DEFEATED! ***")
            break
        if s["in_battle"]:
            e = s.get("enemy_species") or ""
            act = s.get("active_idx", 0)
            party = s["party"]
            eh0 = s.get("enemy_hp")
            # forced replacement: active mon is fainted
            if act < len(party) and party[act]["hp"] == 0:
                if not pick_replacement(emu):
                    print("  team wiped — blackout", flush=True)
                    break                        # real wipe -> stop (retry from checkpoint)
                print(f"  battle t{step}: replacement sent", flush=True)
                continue
            a = party[act]
            pot = s["bag"].get(POTION_ID, 0)
            # SIMPLE & RELIABLE (post-grind): Pikachu L21 leads and ThunderShocks
            # everything (2x on all water mons); Charmeleon L33 finishes via forced
            # replacement if Pikachu falls. Only heal when critically low. Attacks and
            # replacement are the reliable ops — no in-battle switching gymnastics.
            healed = a["hp"] < a["max_hp"] * 0.30 and pot > 0 and use_potion(emu, act)
            if not healed:
                if a["species"] == "PIKACHU":
                    do_turn(emu, 0)                         # ThunderShock (2x)
                elif a["species"] == "CHARMELEON":
                    do_turn(emu, 2)                         # Ember
                else:
                    best, _ = C.best_move(a["moves"], a["species"], e)
                    do_turn(emu, a["moves"].index(best) if best in a["moves"] else 0)
            s2 = emu.snapshot()
            a = s2["party"][s2.get("active_idx", 0)]
            print(f"  battle t{step}: {a['species']} {a['hp']} vs {e} "
                  f"{eh0}->{s2.get('enemy_hp')} act{act}->{s2.get('active_idx')}", flush=True)
            continue

        if s["map"] != 65:
            print("left gym unexpectedly:", s["map"]); break
        pos = (s["x"], s["y"])
        # at the tile right of Misty -> trainers are cleared. Save a pre-Misty
        # checkpoint (fast iteration on just her fight), then talk to start it.
        if pos == (5, 2):
            pre = ROOT / "run/ck-pre-misty.state"
            if not getattr(run, "_saved_pre", False):
                shutil.copy(STATE, pre)
                run._saved_pre = True
                print("saved run/ck-pre-misty.state (trainers cleared)", flush=True)
            # Misty's pre-battle speech is long -> press A until the battle starts
            emu.run("left:4 wait:12")
            for _ in range(12):
                if emu.snapshot()["in_battle"]:
                    break
                emu.run("a:8 wait:130")
            continue
        st = NAV.bfs_path(65, pos, (5, 2))
        if not st:
            print("no BFS path from", pos); break
        emu.run(DIRS[st[0]])                 # one step; battle may trigger on it
        now = (emu.snapshot()["x"], emu.snapshot()["y"])
        if now == pos and not emu.snapshot()["in_battle"]:
            # blocked without a battle (e.g. Misty herself) -> try facing/talking
            emu.run(f"{st[0].replace('u','up').replace('d','down').replace('l','left').replace('r','right')}:4 wait:12 a:8 wait:200 b:8 wait:40")

    s = emu.snapshot()
    print("FINAL badges", bin(s["badges"]), "pos", (s["x"], s["y"]))
    print("party", [(p["species"], p["level"], f"{p['hp']}/{p['max_hp']}") for p in s["party"]])
    if s["badges"] & 2:
        shutil.copy(STATE, ROOT / "run/ck-cascade-badge.state")
        print("saved run/ck-cascade-badge.state")
    return bool(s["badges"] & 2)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", default="run/ck-in-gym-potions.state",
                    help="state to restore before each attempt")
    ap.add_argument("--attempts", type=int, default=10)
    ap.add_argument("--misty-only", action="store_true",
                    help="start from run/ck-pre-misty.state (trainers already cleared)")
    args = ap.parse_args()
    if args.misty_only:
        args.checkpoint = "run/ck-pre-misty.state"
    ck = ROOT / args.checkpoint
    for attempt in range(1, args.attempts + 1):
        print(f"\n===== MISTY ATTEMPT {attempt}/{args.attempts} =====", flush=True)
        shutil.copy(ck, STATE)                    # fresh team each try
        if run():
            print(f"WON on attempt {attempt}")
            break
    else:
        print("no win within attempt budget")
