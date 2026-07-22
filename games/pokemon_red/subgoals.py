"""Walkthrough-derived subgoal tracking: feed the model the CURRENT objective.

The fleet can navigate but can't sequence the game (it got the parcel then wandered,
never delivering it). This supplies the missing spine: an ordered checklist (subgoals.json)
where each entry is the current objective + a grounded hint, shown to the model until its
done-condition (checkable from the RAM snapshot) fires, then the next becomes current.

advance() walks the index forward past already-satisfied goals (monotonic -- never regresses),
so a fleet that already has a starter + parcel lands directly on "deliver the parcel".
"""
import json

import _bootstrap  # noqa

_SUBGOALS = None


def load():
    global _SUBGOALS
    if _SUBGOALS is None:
        p = _bootstrap.GAME_DIR / "subgoals.json"
        _SUBGOALS = json.loads(p.read_text())["subgoals"] if p.exists() else []
    return _SUBGOALS


def _done(sg, s) -> bool:
    d = sg["done"]
    t = d["type"]
    if t == "party_nonempty":
        return bool(s.get("party"))
    if t == "has_item":
        return d["item"] in s.get("bag", {})
    if t == "lost_item":                       # had it, now gone (delivered/used)
        return d["item"] not in s.get("bag", {})
    if t == "reached_map":
        return s.get("map") in d["maps"]
    if t == "badge":
        return bool(s.get("badges", 0) & d["bit"])
    return False


def advance(idx: int, s: dict) -> int:
    """Move the index forward past satisfied subgoals. Sequential: we never test a later
    goal until every earlier one is done, so e.g. deliver-parcel's 'item gone' is only
    checked AFTER get-parcel's 'has item' was true -- no false skips at a fresh start."""
    sgs = load()
    while idx < len(sgs) - 1 and _done(sgs[idx], s):
        idx += 1
    return idx


def current(idx: int):
    sgs = load()
    return sgs[min(idx, len(sgs) - 1)] if sgs else None


def hint_for(sg: dict, s: dict) -> str:
    """Map-conditional hint: a single static hint with both an 'entrance' and an 'inside'
    coordinate makes the model oscillate (it latches the entrance and bounces in/out). So
    a subgoal may carry `hint_by_map` -> the hint for the CURRENT map wins, else `hint`."""
    if not sg:
        return ""
    by_map = sg.get("hint_by_map")
    if by_map:
        m = by_map.get(str(s.get("map")))
        if m:
            return m
    return sg.get("hint", "")


def is_all_done(idx: int, s: dict) -> bool:
    sgs = load()
    return bool(sgs) and idx >= len(sgs) - 1 and _done(sgs[-1], s)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __import__("os").path.dirname(__file__))
    import executor as X
    st = sys.argv[1] if len(sys.argv) > 1 else "run/auto-charmander-q4-0.state"
    import shutil
    shutil.copy(st, "run/_sg.state")
    s = X.Emu("run/_sg.state").snapshot()
    idx = advance(0, s)
    sg = current(idx)
    print(f"state: map={s['map']} party={len(s.get('party',[]))} bag={list(s.get('bag',{}).keys())}")
    print(f"CURRENT SUBGOAL [{idx}] {sg['id']}: {sg['objective']}")
    print(f"HINT: {sg['hint']}")
    __import__("os").remove("run/_sg.state")
