"""Keep the data-gathering fleet alive over long unattended runs.

Every CHECK_EVERY seconds: for each expected run, verify a process with its --tag is
alive; if not, relaunch it from its state file (which persists progress). Also detects
"wedged" runs -- a jsonl whose last-modified time hasn't advanced -- and restarts those
too. Logs every action so we can see what happened while away. Read-only w.r.t. game
state (relaunch just resumes the existing state file).

Run:  GBSKILL_GAME=pokemon_red python3 pipeline/fleet_watchdog.py   (background it)
Stop: pkill -f fleet_watchdog.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa

ROOT = _bootstrap.REPO_ROOT
RUNS = _bootstrap.GAME_DIR / "autoplay_runs"
LOG = ROOT / "run" / "fleet_watchdog.log"
CHECK_EVERY = 180          # seconds between sweeps
WEDGE_AFTER = 600          # jsonl untouched this long => wedged => restart
# v4 = v2's navigation (no heal-collapse) + the starter/dialog skills, verified by the
# regression eval. ONE model for the whole first pass, so the whole fleet runs v4.
V4 = "pokered-8b-v4"
V4Q4 = "pokered-8b-v4-q4"

# (tag, model, state-file) -- the fleet we keep alive.
FLEET = [
    ("charmander-q8-0", V4, "auto-charmander-q8-0.state"),
    ("squirtle-q8-1", V4, "auto-squirtle-q8-1.state"),
    ("bulbasaur-q8-2", V4, "auto-bulbasaur-q8-2.state"),
    ("charmander-q4-0", V4Q4, "auto-charmander-q4-0.state"),
    ("squirtle-q4-1", V4Q4, "auto-squirtle-q4-1.state"),
    ("bulbasaur-q4-2", V4Q4, "auto-bulbasaur-q4-2.state"),
    ("charmander-q4-3", V4Q4, "auto-charmander-q4-3.state"),
    ("squirtle-q4-4", V4Q4, "auto-squirtle-q4-4.state"),
    ("bulbasaur-q4-5", V4Q4, "auto-bulbasaur-q4-5.state"),
    ("charmander-q4-6", V4Q4, "auto-charmander-q4-6.state"),
    ("squirtle-q4-7", V4Q4, "auto-squirtle-q4-7.state"),
    ("bulbasaur-q4-8", V4Q4, "auto-bulbasaur-q4-8.state"),
    # NOTE: self-start dropped for now (v4 loops interact-vs-walk_to at the ball; fix in
    # v5 with more walk_to-a-ball demos). This is a 12-run fleet with ASSIGNED starters
    # (4 Charmander / 4 Squirtle / 4 Bulbasaur), post-lab, goal = beat BROCK.
]


def log(msg: str):
    line = f"[{int(time.time())}] {msg}"
    with LOG.open("a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def alive(tag: str) -> bool:
    # Anchor on the tag at end-of-cmdline. Do NOT put a leading "--" in the pattern:
    # pgrep parses it as an option and the check silently fails (=> false "dead" =>
    # duplicate launches on the same state file => corruption). The tag is the last arg.
    r = subprocess.run(["pgrep", "-f", f"tag {tag}$"], capture_output=True, text=True)
    return bool(r.stdout.strip())


def wedged(tag: str, model: str) -> bool:
    # journal name = <model-with-_>-<tag>.jsonl
    jf = RUNS / f"{model.replace(':', '_')}-{tag}.jsonl"
    if not jf.exists():
        return False
    return (time.time() - jf.stat().st_mtime) > WEDGE_AFTER


def launch(tag: str, model: str, state: str):
    env = dict(os.environ, GBSKILL_GAME="pokemon_red")
    subprocess.Popen(
        ["python3", "pipeline/autoplay.py", "--model", model,
         "--state", str(ROOT / "run" / state), "--steps", "1000000", "--tag", tag],
        stdout=open(ROOT / f"run/autoplay-{tag}.log", "a"),
        stderr=subprocess.STDOUT, cwd=str(ROOT), env=env, start_new_session=True)


def main():
    log(f"watchdog START, guarding {len(FLEET)} runs, sweep={CHECK_EVERY}s")
    while True:
        restarted = []
        for tag, model, state in FLEET:
            if not alive(tag):
                launch(tag, model, state)
                restarted.append(f"{tag}(dead)")
            elif wedged(tag, model):
                subprocess.run(["pkill", "-9", "-f", f"tag {tag}$"])
                time.sleep(1)
                launch(tag, model, state)
                restarted.append(f"{tag}(wedged)")
        if restarted:
            log("restarted: " + ", ".join(restarted))
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    main()
