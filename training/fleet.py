"""Parallel emulator fleet: N workers from the SAME starting checkpoint,
diverged via per-worker RNG jitter (the game's RNG advances every frame, so a
unique startup wait forks the timeline). Each worker plays an expert scripted
policy and logs teacher-quality decision records for dataset building.

Usage:
  python3 fleet.py --state run/ck-route4-post-blackout-fullheal.state \
                   --workers 4 --ticks 250
Records -> training/rollouts/fleet-<worker>-<ts>.jsonl (same schema as eval_live).
"""

import argparse
import json
import multiprocessing as mp
import os
import shutil
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import context as C
import executor as X
import navigate as NAV

ROOT = Path(__file__).resolve().parent.parent
ROLL = Path(__file__).resolve().parent / "rollouts"


def expert_action(s):
    """Ground-truth policy -> (action dict, think str) or None (no decision to log)."""
    if s["in_battle"]:
        active = s["party"][s.get("active_idx", 0)] if s["party"] else None
        if not active:
            return None
        enemy = s.get("enemy_species", "")
        e = C.mon(enemy)
        low = active["hp"] < active["max_hp"] * 0.25
        pp_left = sum(1 for m, p in zip(active["moves"], active["pp"])
                      if p > 0 and (C.move(m) or {}).get("power", 0) > 0)
        if s["in_battle"] == 1 and (low or pp_left == 0):
            why = "HP is too low to risk this wild fight" if low else "no damaging PP left"
            return ({"action": "flee"}, f"{why} — flee and recover.")
        best, ranked = C.best_move(active["moves"], active["species"], enemy)
        if best and ranked and ranked[0][1] > 0:
            m = C.move(best)
            x = C.type_multiplier(m["type"], e["types"]) if e else 1.0
            return ({"action": "fight", "move": best},
                    f"{enemy} is {'/'.join(e['types']) if e else '?'}; {best} is the "
                    f"highest-damage option ({x}x"
                    f"{' +STAB' if m['type'] in (C.mon(active['species']) or {}).get('types', []) else ''}).")
        return ({"action": "flee"}, "No effective attack available — flee.")
    return None


def worker(idx: int, src: str, ticks: int, scratch: str):
    os.nice(10)
    state = Path(scratch) / f"w{idx}.state"
    shutil.copy(src, state)
    emu = X.Emu(state)
    # RNG divergence: unique idle before acting
    emu.run(f"wait:{17 + idx * 53}")
    log = open(ROLL / f"fleet-w{idx}-{os.getpid()}.jsonl", "w")
    battles = 0
    for t in range(ticks):
        s = emu.snapshot()
        act = expert_action(s)
        if act:
            action, think = act
            st, ctx = X.state_text(s), X.ctx_for(s)
            log.write(json.dumps({
                "scenario": f"fleet-w{idx}", "step": t,
                "facts": C.build_facts(ctx), "state_text": st, "ctx": ctx,
                "goal": "Train safely through this area; flee when at risk.",
                "action": action, "think": think, "teacher": True,
                "snap": {k: v for k, v in s.items()}}) + "\n")
            log.flush()
            emu.do(action, s)
            if s["in_battle"]:
                battles += 1
            continue
        # random-walk the current map on walkable tiles to trigger encounters
        gg = NAV.grid(s["map"])
        moved = False
        if gg:
            g, w, h = gg
            opts = []
            for d, (dx, dy) in (("left:16 wait:8", (-1, 0)), ("right:16 wait:8", (1, 0)),
                                 ("up:16 wait:8", (0, -1)), ("down:16 wait:8", (0, 1))):
                nx, ny = s["x"] + dx, s["y"] + dy
                if 0 <= nx < w and 0 <= ny < h and g[ny][nx]:
                    opts.append(d)
            if opts:
                import random as _r
                emu.run(_r.Random(t * 7 + idx).choice(opts))
                moved = True
        if not moved:
            emu.run("b:8 wait:40")           # dialog/edge unstick
        if t % 12 == 11:
            emu.run("b:8 wait:40")
    log.write(json.dumps({"scenario": f"fleet-w{idx}", "result": "done",
                          "battle_steps": battles}) + "\n")
    log.close()
    print(f"worker {idx}: {battles} battle decisions logged", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(ROOT / "run/ck-route4-grind.state"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--ticks", type=int, default=250)
    args = ap.parse_args()
    ROLL.mkdir(exist_ok=True)
    scratch = Path(os.environ.get("TMPDIR", "/tmp")) / f"fleet-{int(time.time())}-{os.getpid()}"
    scratch.mkdir(parents=True)
    procs = [mp.Process(target=worker, args=(i, args.state, args.ticks, str(scratch)))
             for i in range(args.workers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    print("fleet done")


if __name__ == "__main__":
    main()
