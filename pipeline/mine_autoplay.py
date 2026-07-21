"""DAgger miner over the autonomous-fleet logs (training/autoplay_runs/*.jsonl).

Each logged decision is (facts, state, goal) -> the model's action. Where the
correct action is DERIVABLE from game rules, we emit a teacher example: a
CORRECTION when the model was wrong, a CONFIRMATION when it was right. These feed
the v3 dataset (up-weighted 3x, like v2's DAgger pass).

Rock-solid teacher signals (unambiguous ground truth):
  - in battle: fight the type-optimal move (context.best_move); the model often
    even omits the move.
  - overworld + active mon HP < 30%: heal_at_center (the fleet's #1 failure — mons
    black out because the model never heals).
  - overworld + a BATTLE action (switch/flee/throw_ball/fight): invalid; navigate
    toward the objective instead.

Usage: python3 mine_autoplay.py            # prints counts + samples
       imported: mine_autoplay.mine() -> list of SFT examples
"""

import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402
import context as C
import navigate as NAV
import prompts

RUNS = _bootstrap.GAME_DIR / "autoplay_runs"
HP_RE = re.compile(r"HP (\d+)/(\d+)")
BATTLE_ACTS = {"switch", "flee", "throw_ball", "fight"}


def _facts_state_goal(r):
    facts = r.get("facts", "")
    st = r.get("state_text", "")
    goal = r.get("goal", "")
    return facts, st, goal


def _ex(r, think, action, kind):
    facts, st, goal = _facts_state_goal(r)
    ex = prompts.format_example(facts, st, goal, think, json.dumps(action))
    ex["meta"] = {"kind": kind}
    return ex


def _active_hp(r):
    """(hp, maxhp) of the lead/active mon from state_text, or None."""
    m = HP_RE.search(r.get("state_text", ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _goal_direction(goal, map_id):
    """Which connected edge heads toward the objective? Early game -> north."""
    conns = NAV.connections(map_id)
    g = goal.lower()
    # crude but effective: the whole Kanto early route is northward to Pewter
    for d in ("north", "east", "west", "south"):
        if d in conns and (d in g or (d == "north" and "pewter" in g or "brock" in g)):
            return d
    return "north" if "north" in conns else (next(iter(conns), None))


def teacher(r):
    """-> (think, action, kind) correction/confirmation, or None if no ground truth."""
    ctx = r.get("ctx", {})
    act = r.get("action", {})
    a = act.get("action")
    snap = r.get("snap", {})
    in_battle = ctx.get("in_battle") or snap.get("in_battle")

    # 1) In battle: fight the type-optimal move.
    if in_battle:
        enemy = ctx.get("enemy_species") or snap.get("enemy_species") or ""
        our = ctx.get("our_species", "")
        moves = ctx.get("our_moves", [])
        if enemy and our and moves:
            best, ranked = C.best_move(moves, our, enemy)
            if best and ranked and ranked[0][1] > 0:
                e = C.mon(enemy)
                etypes = "/".join(e["types"]) if e else "?"
                think = (f"{enemy} is {etypes}. Among {', '.join(moves)}, {best} scores "
                         f"highest by type/power — use it.")
                action = {"action": "fight", "move": best}
                right = a == "fight" and act.get("move") == best
                return think, action, ("battle_ok" if right else "battle_fix")
        return None

    # 2) Overworld, critically low HP -> heal.
    hp = _active_hp(r)
    if hp and hp[1] and hp[0] < 0.3 * hp[1]:
        think = (f"My lead is at {hp[0]}/{hp[1]} HP (<30%) and I'm in the overworld — "
                 f"one more fight risks a blackout. Go heal at a Pokémon Center first.")
        action = {"action": "heal_at_center"}
        return think, action, ("heal_ok" if a == "heal_at_center" else "heal_fix")

    # 3) Overworld but a BATTLE action -> invalid; navigate toward the goal.
    if a in BATTLE_ACTS:
        mid = None
        name2id = {e["name"]: m for m, e in NAV.registry().items()}
        mid = name2id.get(ctx.get("map_name"))
        d = _goal_direction(r.get("goal", ""), mid) if mid is not None else None
        gg = NAV.grid(mid) if mid is not None else None
        if d and gg:
            _g, w, h = gg
            tx, ty = {"north": (snap.get("x", w // 2), 0),
                      "south": (snap.get("x", w // 2), h - 1),
                      "west": (0, snap.get("y", h // 2)),
                      "east": (w - 1, snap.get("y", h // 2))}[d]
            think = (f"I'm in the overworld, not a battle — {a} does not apply here. To make "
                     f"progress toward my goal I should travel {d} to the next area.")
            return think, {"action": "walk_to", "x": tx, "y": ty}, "overworld_battleact_fix"
    return None


def mine():
    out = []
    # recurse so archived harvests (autoplay_runs/harvest*/) are included too
    for f in sorted(glob.glob(str(RUNS / "**" / "*.jsonl"), recursive=True)):
        for line in open(f):
            if '"action"' not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            t = teacher(r)
            if t:
                think, action, kind = t
                out.append(_ex(r, think, action, kind))
    return out


if __name__ == "__main__":
    xs = mine()
    from collections import Counter
    c = Counter(x["meta"]["kind"] for x in xs)
    print(f"{len(xs)} teacher examples from autoplay fleet logs")
    for k, n in c.most_common():
        print(f"  {n:5d}  {k}")
    if xs:
        fixes = [x for x in xs if x["meta"]["kind"].endswith("fix")]
        if fixes:
            print("\n--- sample CORRECTION ---")
            print(fixes[0]["messages"][1]["content"][:200])
            print(fixes[0]["messages"][2]["content"])
