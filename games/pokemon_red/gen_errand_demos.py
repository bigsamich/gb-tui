"""Generate clean ERRAND demos: (STATE + subgoal GOAL/hint) -> correct high-level action.

v4 gets stuck sequencing the parcel errand (won't leave Oak, picks off-map targets inside
buildings). The fleet's own logs are too noisy to mine (mostly stuck press-spam). So we
DRIVE the correct sequence deterministically and label every high-level decision with the
exact prompt the fleet sees at inference (facts incl. the subgoal GUIDE hint + the subgoal
objective) -> the right action. Distilling these teaches v5 to execute the spine reliably.

Sequence: (has parcel) go south to Pallet -> enter lab (12,11) -> walk to Oak (5,4) ->
interact (deliver) ; (no parcel) exit lab (5,11) -> north Pallet->Route1->Viridian->Route2.

Output: games/pokemon_red/data_demos/errand.jsonl (train-only, weighted in build/make_v5).
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
import prompts
import subgoals as SG

ROOT = _bootstrap.REPO_ROOT
OUT = Path(__file__).resolve().parent / "data_demos" / "errand.jsonl"

# map ids
LAB, PALLET, ROUTE1, VIRIDIAN, ROUTE2 = 40, 0, 12, 1, 13
OAK = (5, 4)          # stand here, face up, interact to deliver
LAB_EXIT = (5, 11)    # step onto to leave the lab


def correct_action(s):
    """The right high-level action for the errand, given the live snapshot."""
    m, x, y = s["map"], s["x"], s["y"]
    has_parcel = 70 in s.get("bag", {})
    if has_parcel:                                  # deliver: reach Oak in the lab
        if m == LAB:
            return {"action": "interact"} if (x, y) == OAK else {"action": "walk_to", "x": OAK[0], "y": OAK[1]}
        if m == PALLET:
            return {"action": "walk_to", "x": 12, "y": 11}          # lab entrance
        if m == VIRIDIAN:
            return {"action": "walk_to", "x": 23, "y": 35}          # south edge -> Route1
        if m == ROUTE1:
            return {"action": "walk_to", "x": 10, "y": 35}          # south edge -> Pallet
        return {"action": "walk_to", "x": 10, "y": 35}
    # delivered: head NORTH to Route 2
    if m == LAB:
        return {"action": "walk_to", "x": LAB_EXIT[0], "y": LAB_EXIT[1]}
    if m == PALLET:
        return {"action": "walk_to", "x": 10, "y": 0}               # north edge -> Route1
    if m == ROUTE1:
        return {"action": "walk_to", "x": 10, "y": 0}               # north edge -> Viridian
    if m == VIRIDIAN:
        return {"action": "walk_to", "x": 18, "y": 0}               # north edge -> Route2
    return None


def gen_from(seed: Path, tag: str) -> list[dict]:
    tmp = ROOT / "run" / f"_errand_{tag}.state"
    shutil.copy(seed, tmp)
    e = X.Emu(str(tmp))
    by_id = {sg["id"]: sg for sg in SG.load()}
    examples = []
    last, stuck = None, 0
    for _ in range(22):
        s = e.snapshot()
        if s["map"] == ROUTE2:                       # reached the goal of this errand
            break
        key = (s["map"], s["x"], s["y"], 70 in s.get("bag", {}))
        stuck = stuck + 1 if key == last else 0
        last = key
        if stuck >= 4:                               # not progressing -> stop this seed
            break
        act = correct_action(s)
        if act is None:
            break
        # pick the subgoal by ACTUAL phase (has parcel => deliver, else => reach-route2);
        # SG.advance(0,..) would mis-label delivered seeds due to the transient-item done.
        sg = by_id["deliver-parcel"] if 70 in s.get("bag", {}) else by_id["reach-route2"]
        st = X.state_text(s)
        facts = C.build_facts(X.ctx_for(s))
        hint = SG.hint_for(sg, s)
        if hint:
            facts = f"GUIDE: {hint}" + ("\n" + facts if facts else "")
        goal = sg["objective"] if sg else ""
        think = f"Current objective: {goal[:60]}. The right next move here is {act['action']}."
        examples.append(prompts.format_example(facts, st, goal, think, json.dumps(act)))
        # execute; on the Oak interact, advance the delivery dialogue to completion
        e.do(act, s)
        if act["action"] == "interact" and s["map"] == LAB:
            for _ in range(14):
                if 70 not in e.snapshot().get("bag", {}):
                    break
                e.run("a:8 wait:100")
    got_north = e.snapshot()["map"] in (ROUTE2, 2)
    delivered = 70 not in e.snapshot().get("bag", {})
    tmp.unlink(missing_ok=True)
    print(f"  {tag}: {len(examples)} demos | delivered={delivered} reached_route2/pewter={got_north}")
    return examples


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    seeds = sorted((ROOT / "run").glob("auto-*.state"))
    f = OUT.open("w")          # write INCREMENTALLY so a timeout still leaves partial data
    total = 0
    for seed in seeds:
        for ex in gen_from(seed, seed.stem.replace("auto-", "")):
            f.write(json.dumps(ex) + "\n")
            total += 1
        f.flush()
    f.close()
    print(f"wrote {total} errand demos -> {OUT}")


if __name__ == "__main__":
    main()
