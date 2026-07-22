"""Generate perception-grounded dialog demos: SCREEN -> button.

The point of this file: teach the model to DRIVE dialogs/menus by reading the screen,
instead of running a blind fixed-timing macro. We already KNOW the button that advances
each frame of the starter-acquisition dialog. So we step through it one button at a time,
and at every step record (FACTS + STATE-with-SCREEN + GOAL -> that button) as a training
example. The label is grounded in what actually advances the on-screen dialog -- this is
legitimate teaching (like the type-chart teacher), not hand-driving the live model.

After distilling these in, the model presses the right button because it READS the screen;
the macro in executor.interact is no longer needed.

Output: games/pokemon_red/data_demos/dialog.jsonl  (train-only, weighted in build_dataset)
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
import _bootstrap  # noqa
import executor as X
import context as C
import prompts  # noqa: F401  (parity with the training prompt format)
import dialog_teacher as DT

ROOT = _bootstrap.REPO_ROOT
OUT = Path(__file__).resolve().parent / "data_demos" / "dialog.jsonl"

# (ball interaction tile, starter) -- round-robin so no starter is privileged.
BALLS = [((6, 4), "CHARMANDER"), ((7, 4), "SQUIRTLE"), ((8, 4), "BULBASAUR")]

GOAL = ("Get your FIRST Pokemon: choose a starter from the three Poke Balls in Oak's Lab, "
        "then win the rival battle that follows.")


def press_button(emu: X.Emu, btn: str):
    """Press ONE primitive button (with settle wait) and save state."""
    emu.run(f"{btn}:8 wait:120")


def gen_for_ball(base_state: Path, tile, species: str) -> list[dict]:
    """Walk to a ball, then step through the offer dialog one button at a time,
    recording (state-with-SCREEN -> button) at each step."""
    tmp = ROOT / "run" / f"_gendlg_{species}.state"
    shutil.copy(base_state, tmp)
    e = X.Emu(str(tmp))
    # navigate below the ball (uses the general BFS walk_to; clears Oak's intro if needed)
    for _ in range(8):
        s = e.snapshot()
        if (s["x"], s["y"]) == tile:
            break
        e.do({"action": "walk_to", "x": tile[0], "y": tile[1]}, s)
    # face up and press A once to OPEN the dialog (this is executor.interact's opener)
    e.run("up:4 wait:12 a:8 wait:120")

    # Drive the whole offer dialog by PERCEPTION: at each frame ask the teacher what button
    # the on-screen text calls for, record (state -> button), press it. Continue until the
    # screen clears (dialog done) -- this captures the nickname decline too, and every label
    # matches the screen it was read from.
    examples = []
    for _ in range(14):
        s = e.snapshot()
        lesson = DT.teach(s.get("screen_text", ""), s.get("screen_menu", False), GOAL)
        if lesson is None:
            break                                       # dialog fully closed
        btn, think = lesson
        st = X.state_text(s)
        ctx = X.ctx_for(s)
        facts = C.build_facts(ctx)
        action_json = json.dumps({"action": "press", "buttons": f"{btn}:8 wait:60"})
        examples.append(prompts.format_example(facts, st, GOAL, think, action_json))
        press_button(e, btn)
    got = e.snapshot().get("party")
    tmp.unlink(missing_ok=True)
    print(f"  {species}: {'GOT ' + got[0]['species'] if got else 'no-mon'} "
          f"({len(examples)} dialog examples)")
    return examples


def main():
    base = ROOT / "run" / "ck-lab-before-starter.state"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    all_ex = []
    for tile, species in BALLS:
        all_ex.extend(gen_for_ball(base, tile, species))
    with OUT.open("w") as f:
        for ex in all_ex:
            f.write(json.dumps(ex) + "\n")
    print(f"wrote {len(all_ex)} dialog demos -> {OUT}")


if __name__ == "__main__":
    main()
