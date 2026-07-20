"""Live scenario evaluation: the model plays real game situations from saved
checkpoints; outcomes are RAM-verified. Every decision is logged to
training/rollouts/ for v2 dataset building (DAgger-style corrections).

Usage:
  python3 eval_live.py --model pokered-8b [--scenario NAME]
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context as C
import executor as X
import prompts
from serve_shim import ask, extract_action

ROOT = Path(__file__).resolve().parent.parent
RUNS = Path(__file__).resolve().parent / "rollouts"
SCEN = Path(__file__).resolve().parent / "scenarios"


def bake_wild_battle(out: Path) -> bool:
    """Walk the Route 4 grass from a healed checkpoint until a wild battle starts."""
    src = ROOT / "run/ck-route4-grind.state"
    if not src.exists():
        src = ROOT / "run/ck-route4-post-blackout-fullheal.state"
    shutil.copy(src, out)
    emu = X.Emu(out)
    for _ in range(40):
        s = emu.snapshot()
        if s["in_battle"] == 1:
            return True
        x = s["x"]
        emu.run("left:16 wait:8" if (x % 8) < 4 else "right:16 wait:8")
    return False


SCENARIOS = {
    "nav-enter-pc": {
        "state": ROOT / "run/ck-CERULEAN.state",
        "goal": "Enter the Pokémon Center in this city.",
        "success": lambda s: s["map"] == 64,
        "fail": lambda s: any(p["hp"] == 0 for p in s["party"]),
        "cap": 10,
    },
    "wild-battle": {
        "bake": bake_wild_battle,
        "goal": "Win this wild battle. Do not let any Pokémon faint. Catch only Pikachu, nothing else.",
        "success": lambda s: s["in_battle"] == 0 and all(p["hp"] > 0 for p in s["party"]),
        "fail": lambda s: any(p["hp"] == 0 for p in s["party"]),
        "cap": 8,
    },
    "catch-pikachu": {
        "state": ROOT / "run/ck-PIKACHU-encounter.state",
        "goal": "Catch this wild Pikachu. It must not faint and must be caught.",
        "success": lambda s, n0=[None]: (n0.__setitem__(0, s["party_n"]) or False)
                    if n0[0] is None else s["party_n"] > n0[0],
        "fail": lambda s: s["in_battle"] == 0,   # battle ended without catching
        "cap": 10,
    },
}


def run_scenario(name, spec, model, url):
    RUNS.mkdir(exist_ok=True)
    SCEN.mkdir(exist_ok=True)
    work = SCEN / f"{name}.state"
    if "bake" in spec:
        baked = SCEN / f"{name}.baked.state"
        if not baked.exists():
            if not spec["bake"](baked):
                return {"scenario": name, "result": "bake-failed"}
        shutil.copy(baked, work)
    else:
        if not spec["state"].exists():
            return {"scenario": name, "result": "missing-checkpoint"}
        shutil.copy(spec["state"], work)

    emu = X.Emu(work)
    log_path = RUNS / f"{model.replace(':','_')}-{name}-{int(time.time())}.jsonl"
    log = open(log_path, "w")
    result = "cap-reached"
    s = emu.snapshot()
    spec["success"](s)   # prime stateful predicates
    for step in range(spec["cap"]):
        s = emu.snapshot()
        if spec["success"](s):
            result = "SUCCESS"
            break
        if spec.get("fail") and spec["fail"](s) and step > 0:
            result = "FAILED"
            break
        st, ctx = X.state_text(s), X.ctx_for(s)
        facts = C.build_facts(ctx)
        user = (f"[FACTS]\n{facts}\n\n" if facts else "") + \
               f"[STATE]\n{st}\n\n[GOAL] {spec['goal']}"
        msgs = [{"role": "system", "content": prompts.SYSTEM},
                {"role": "user", "content": user}]
        raw = ask(model, msgs, url)
        act = extract_action(raw) or {"action": "press", "buttons": "b:8 wait:60"}
        res = emu.do(act, s)
        log.write(json.dumps({"scenario": name, "step": step, "facts": facts,
                              "state_text": st, "ctx": ctx, "goal": spec["goal"],
                              "model_raw": raw[-600:], "action": act,
                              "exec": res, "snap": {k: v for k, v in s.items()}}) + "\n")
        log.flush()
        print(f"  [{name}] step {step}: {act} -> {res}")
    final = emu.snapshot()
    if result == "cap-reached" and spec["success"](final):
        result = "SUCCESS"
    log.write(json.dumps({"scenario": name, "result": result,
                          "final": {k: v for k, v in final.items()}}) + "\n")
    log.close()
    return {"scenario": name, "result": result, "log": str(log_path)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="pokered-8b")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--scenario")
    args = ap.parse_args()
    results = []
    for name, spec in SCENARIOS.items():
        if args.scenario and name != args.scenario:
            continue
        print(f"== scenario: {name}")
        r = run_scenario(name, spec, args.model, args.url)
        print("  ->", r["result"])
        results.append(r)
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
