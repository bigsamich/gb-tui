"""Teacher DEMONSTRATIONS of the opening 'choose a starter' sequence, for DAgger.

The model can't do this scene yet (it emits garbage `fight`s). So we hand-guide the
CORRECT high-level actions from the pre-starter state and log each as a training
example — the model learns to CHOOSE a starter and take it. We demonstrate all
three starters (round-robin) so no choice is privileged: the lesson is the action
format (walk to a ball -> interact), not which mon.

The mechanically-proven button macros drive execution; the LOGGED action is the
clean high-level one the model should emit. Output -> training/data_demos/starter.jsonl
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402
import context as C
import executor as X
import navigate as NAV
import prompts
import autoplay

ROOT = _bootstrap.REPO_ROOT
SRC = ROOT / "run/ck-lab-before-starter.state"
OUT = Path(__file__).resolve().parent / "data_demos"
OUT.mkdir(exist_ok=True)
LAB = 40
BALL = {"CHARMANDER": (6, 4), "SQUIRTLE": (7, 4), "BULBASAUR": (8, 4)}
TYPE = {"CHARMANDER": "Fire", "SQUIRTLE": "Water", "BULBASAUR": "Grass"}


def prompt_parts(emu):
    s = emu.snapshot()
    st, ctx = X.state_text(s), X.ctx_for(s)
    facts = C.build_facts(ctx)
    if not s.get("party"):
        facts = autoplay.STARTER_FACTS + ("\n" + facts if facts else "")
        goal = autoplay.objective(s["badges"], has_party=False)
    else:
        goal = autoplay.objective(s["badges"], has_pokedex=False,
                                  has_parcel=False, has_party=True)
    return s, facts, st, goal


def ex(facts, st, goal, think, action):
    e = prompts.format_example(facts, st, goal, think, json.dumps(action))
    e["meta"] = {"kind": "demo_starter"}
    return e


def clear_oak(emu):
    for _ in range(20):
        b = emu.snapshot()
        emu.run("b:8 wait:70")
        emu.run("down:16 wait:14")
        if (emu.snapshot()["x"], emu.snapshot()["y"]) != (b["x"], b["y"]):
            return True
    return False


def take_starter(emu):
    """Proven acquisition macro from the tile below the chosen ball."""
    emu.run("up:4 wait:12 a:8 wait:160 a:8 wait:160 a:8 wait:200 a:8 wait:200 "
            "a:8 wait:160 a:8 wait:200 down:4 wait:16 a:8 wait:160 b:8 wait:120 b:8 wait:120")


def demo(starter, seed):
    out = OUT / f".work-{starter}-{seed}.state"
    shutil.copy(SRC, out)
    emu = X.Emu(out)
    bx, by = BALL[starter]
    exs = []

    # 1) CHOOSE: log (state -> walk_to the chosen ball). This is the real decision.
    s, facts, st, goal = prompt_parts(emu)
    think = (f"I have no Pokémon yet. I'll choose {starter} ({TYPE[starter]}) as my starter — "
             f"walk to its Poké Ball at ({bx},{by-1}) to pick it.")
    exs.append(ex(facts, st, goal, think, {"action": "walk_to", "x": bx, "y": by}))
    clear_oak(emu)
    for _ in range(4):
        p = emu.snapshot()
        if (p["x"], p["y"]) == (bx, by):
            break
        stp = NAV.bfs_path(LAB, (p["x"], p["y"]), (bx, by))
        if stp:
            emu.run(NAV.steps_to_script(stp, cap=8))

    # 2) TAKE: log (state -> interact) to grab the ball
    s, facts, st, goal = prompt_parts(emu)
    think = f"I'm right below the {starter} ball. Interact to take it as my starter."
    exs.append(ex(facts, st, goal, think, {"action": "interact"}))
    take_starter(emu)
    got = emu.snapshot()["party"]
    if not got or got[0]["species"] != starter:
        print(f"  [{starter}-{seed}] acquisition FAILED (party={[(p['species']) for p in got]})")
        return []

    # 3) POST-ACQUISITION: the rival grabs the type-advantage starter and battles you
    #    (it triggers as you head for the exit). Unified loop: fight when in battle,
    #    else advance Oak's speech + move toward the exit. Log every high-level action.
    exits = [(x, y) for x, y, lbl in C.map_warps("OaksLab") if lbl.startswith("Exit")]
    door = exits[0] if exits else (4, 11)
    for _ in range(40):
        s = emu.snapshot()
        if s["map"] != LAB:
            break                                   # left the lab -> done
        if s["in_battle"]:
            a = s["party"][s.get("active_idx", 0)]
            enemy = s.get("enemy_species", "")
            best, _r = C.best_move(a["moves"], a["species"], enemy) if a["moves"] else (None, None)
            move = best or (a["moves"][0] if a["moves"] else None)
            _s2, facts, st, goal = prompt_parts(emu)
            think = f"Rival battle with my new {starter}. Enemy {enemy}; {move} is my best attack."
            exs.append(ex(facts, st, goal, think, {"action": "fight", "move": move}))
            slot = a["moves"].index(move) if move in a["moves"] else 0
            emu.run(X._attack_script(slot))
        else:
            _s, facts, st, goal = prompt_parts(emu)
            think = ("Got my starter (and the rival battle is done). Head to the lab Exit and "
                     "leave to start the journey.")
            exs.append(ex(facts, st, goal, think, {"action": "walk_to", "x": door[0], "y": door[1]}))
            emu.run("b:8 wait:80 down:16 wait:16")   # advance any speech + nudge toward exit
            p = emu.snapshot()
            stp = NAV.bfs_path(LAB, (p["x"], p["y"]), door)
            if stp:
                emu.run(NAV.steps_to_script(stp, cap=10) + " down:16 wait:30")
    out.unlink(missing_ok=True)
    return exs


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    all_ex = []
    order = ["CHARMANDER", "SQUIRTLE", "BULBASAUR"]
    for r in range(reps):
        st = order[r % 3]
        e = demo(st, r)
        print(f"  demo {st}-{r}: {len(e)} examples")
        all_ex.extend(e)
    (OUT / "starter.jsonl").write_text("\n".join(json.dumps(e) for e in all_ex) + "\n")
    print(f"\n{len(all_ex)} starter-demo examples -> {OUT/'starter.jsonl'}")


if __name__ == "__main__":
    main()
