"""Generate self-navigation demos by RELABELING the no-hints fleet's decisions.

Every no-hints decision logged its perception (GEOGRAPHY / EXPLORE) in the facts. That
perception is deterministic and correct (map-graph router + exploration memory), so the
right ACTION is simply 'follow the perception'. We parse each logged (facts, state), derive
the perception-implied action, and emit a clean (facts+state+goal -> action) demo. This
bakes reliable perception-following into v7 so the model self-navigates with less wandering
-- no hints, no hardcoded locations, just 'do what the perception says'.

Output: games/pokemon_red/data_demos/selfnav.jsonl
"""
import json
import glob
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
import _bootstrap  # noqa
import navigate as NAV
import prompts

GAME = _bootstrap.GAME_DIR
OUT = GAME / "data_demos" / "selfnav.jsonl"
CAP = 6000


def _edge_tile(map_id, x, y, d):
    g = NAV.grid(map_id)
    if not g:
        return None
    _grid, w, h = g
    if d == "north":
        return (x, 0)
    if d == "south":
        return (x, h - 1)
    if d == "west":
        return (0, y)
    if d == "east":
        return (w - 1, y)
    return None


def perception_action(facts, s):
    """The action the logged perception points to, or None."""
    m = s["map"]
    x, y = s["x"], s["y"]
    # room exploration: the EXPLORE line literally says 'walk_to (fx,fy)'
    if "your objective is somewhere in THIS area" in facts:
        r = re.search(r"walk_to \((\d+),(\d+)\)", facts)
        if r:
            return {"action": "walk_to", "x": int(r.group(1)), "y": int(r.group(2))}, \
                   "Cover unexplored ground to find the objective in this room."
    # warp maze: prefer the exit that leads toward the goal, else an untried exit
    if "EXPLORE (exits on this map)" in facts:
        t = re.search(r"\((\d+),(\d+)\) -> \w+ <== leads toward", facts)
        if t:
            return {"action": "walk_to", "x": int(t.group(1)), "y": int(t.group(2))}, \
                   "This exit leads toward my objective -- take it."
        u = re.search(r"\((\d+),(\d+)\) -> \? UNTRIED", facts)
        if u:
            return {"action": "walk_to", "x": int(u.group(1)), "y": int(u.group(2))}, \
                   "I have not tried this exit -- explore it to find the way."
    # geography: leave by an edge toward the goal
    g = re.search(r"beyond the (\w+) edge", facts)
    if g:
        t = _edge_tile(m, x, y, g.group(1).lower())
        if t:
            return {"action": "walk_to", "x": t[0], "y": t[1]}, \
                   f"My objective is beyond the {g.group(1).lower()} edge -- head there."
    # geography via a specific warp / building exit
    w = re.search(r"(?:stairs at|Exit at) \((\d+),\s*(\d+)\)", facts)
    if w:
        return {"action": "walk_to", "x": int(w.group(1)), "y": int(w.group(2))}, \
               "Take this exit toward my objective."
    return None


def main():
    seen, examples = set(), []
    for f in glob.glob(str(GAME / "autoplay_runs" / "pokered-8b-v6*.jsonl")):
        for line in open(f):
            if '"snap"' not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            s = r["snap"]
            if s.get("in_battle") or r.get("mode"):        # nav only; skip battle/teacher steps
                continue
            facts = r.get("facts", "")
            act = perception_action(facts, s)
            if act is None:
                continue
            action, think = act
            st = r.get("state_text", "")
            goal = r.get("goal", "")
            key = (st, action.get("x"), action.get("y"))
            if key in seen:
                continue
            seen.add(key)
            examples.append(prompts.format_example(facts, st, goal, think, json.dumps(action)))
            if len(examples) >= CAP:
                break
        if len(examples) >= CAP:
            break
    with OUT.open("w") as fh:
        for ex in examples:
            fh.write(json.dumps(ex) + "\n")
    print(f"wrote {len(examples)} self-nav demos -> {OUT}")


if __name__ == "__main__":
    main()
