"""Launch a fleet of AUTONOMOUS model-driven runs, round-robin over starters, each
on its own state copy (so the shared start states aren't mutated). Goal: reach
Brock then Misty, entirely by the model. Runs until killed; monitor progress via
the per-run logs and training/autoplay_runs/*.jsonl.

Usage: python3 fleet_auto.py [N] [model] [start]
  start = "post" (default) -> begin at Pallet with a baked starter (round-robin)
          "pre"            -> begin in Oak's Lab with NO Pokémon, so the MODEL picks
                              its own starter (true from-the-start run; needs a model
                              trained on the starter scene, i.e. v3+).
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402

ROOT = _bootstrap.REPO_ROOT
STARTERS = ["charmander", "squirtle", "bulbasaur"]

N = int(sys.argv[1]) if len(sys.argv) > 1 else 6
MODEL = sys.argv[2] if len(sys.argv) > 2 else "pokered-8b-v2"
START = sys.argv[3] if len(sys.argv) > 3 else "post"
STEPS = 1_000_000          # effectively unbounded; we stop by wall-clock
PRE = ROOT / "run/ck-lab-before-starter.state"

procs = []
for i in range(N):
    st = STARTERS[i % len(STARTERS)]
    work = ROOT / f"run/auto-{st}-{i}.state"
    src = PRE if START == "pre" else ROOT / f"run/start-{st}.state"
    shutil.copy(src, work)
    tag = f"{st}-{i}"
    log = ROOT / f"run/autoplay-{tag}.log"
    p = subprocess.Popen(
        ["python3", str(ROOT / "pipeline/autoplay.py"),
         "--model", MODEL, "--state", str(work), "--steps", str(STEPS), "--tag", tag],
        stdout=open(log, "w"), stderr=subprocess.STDOUT, cwd=str(ROOT))
    procs.append((tag, p.pid))
    print(f"launched {tag} pid={p.pid} -> run/autoplay-{tag}.log", flush=True)
    time.sleep(4)   # stagger so Ollama requests don't all collide at t0

print(f"\nfleet up: {len(procs)} autonomous runs on {MODEL}.")
print("pids:", " ".join(str(pid) for _, pid in procs))
