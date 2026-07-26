"""Generate forest/gate navigation demos by RELABELING mined fleet states.

The fleet reached Pewter but only via hand-supplied waypoints, and it wandered backward
after. v6 should learn the forest -> north gate -> Pewter path. The fleet's journals hold
tens of thousands of states in exactly these maps -- mostly noisy/stuck, but the noise
doesn't matter because we RELABEL each with the deterministically-correct waypoint. Facts
are regenerated from the snapshot with the CURRENT subgoal hints (train == inference).

Correct action per map (the confirmed path charmander-q4-6 took to Pewter):
  ViridianForest (51) -> walk_to (1,0)  (north-gate warp)
  ForestNorthGate (47) -> walk_to (4,0) (exit north to Pewter)
  ForestSouthGate (50) -> walk_to (4,0) (north into the forest)
  PewterCity (2) -> walk_to (16,17)     (Pewter Gym entrance)
Battle states are skipped (battle behavior is separate).

Output: games/pokemon_red/data_demos/forest.jsonl
"""
import json
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
import _bootstrap  # noqa
import executor as X
import context as C
import prompts
import subgoals as SG

GAME = _bootstrap.GAME_DIR
OUT = GAME / "data_demos" / "forest.jsonl"

WAYPOINT = {51: (1, 0), 47: (4, 0), 50: (4, 0), 2: (16, 17)}
CAP_PER_MAP = 250


def main():
    tf = [sg for sg in SG.load() if sg["id"] == "through-forest"][0]
    seen, per_map, examples = set(), {}, []
    for f in glob.glob(str(GAME / "autoplay_runs" / "pokered-8b-v5*.jsonl")):
        for line in open(f):
            if '"snap"' not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            s = r["snap"]
            m = s.get("map")
            if m not in WAYPOINT or s.get("in_battle"):
                continue
            tx, ty = WAYPOINT[m]
            if (s["x"], s["y"]) == (tx, ty):        # already on the target; nothing to teach
                continue
            act = {"action": "walk_to", "x": tx, "y": ty}
            # regenerate the prompt from the snapshot with CURRENT hints (parity)
            st = X.state_text(s)
            facts = C.build_facts(X.ctx_for(s))
            hint = SG.hint_for(tf, s)
            if hint:
                facts = f"GUIDE: {hint}" + ("\n" + facts if facts else "")
            key = (st, tx, ty)
            if key in seen:
                continue
            if per_map.get(m, 0) >= CAP_PER_MAP:
                continue
            seen.add(key)
            per_map[m] = per_map.get(m, 0) + 1
            think = (f"I'm in this area heading to Pewter. The right move is to walk toward "
                     f"the exit at ({tx},{ty}).")
            examples.append(prompts.format_example(facts, st, tf["objective"],
                                                   think, json.dumps(act)))
    with OUT.open("w") as fh:
        for ex in examples:
            fh.write(json.dumps(ex) + "\n")
    print(f"per-map demos: {per_map}")
    print(f"wrote {len(examples)} forest/gate demos -> {OUT}")


if __name__ == "__main__":
    main()
