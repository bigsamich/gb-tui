"""reward — generic default reward signal (game-agnostic).

Shaping (from the spec):
  + new map/area/state visited (exploration / novelty)
  + score / money / party / inventory / XP up
  + persistent story flag flipped
  + next subgoals.json item satisfied (dense per-milestone bonus)
  - died / blacked-out
  - wedged in one spot for N steps

Games may override ``GameAdapter.reward()`` when they can do better; the walkthrough
checklist supplies the dense milestone bonus.

TODO: flesh out the counter/flag-delta detection and subgoal matching. The current
``step_reward`` is a minimal, safe placeholder (exploration + wedge penalty only).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402

WEDGE_LIMIT = 8


def step_reward(before: dict, after: dict, seen_keys=None, stall: int = 0) -> float:
    """Minimal generic reward. before/after are adapter snapshot dicts.

    TODO: add money/party/inventory/XP/flag deltas and subgoal-satisfaction bonus.
    """
    r = 0.0
    if seen_keys is not None:
        key = (after.get("map"), after.get("x"), after.get("y"))
        if key not in seen_keys:
            r += 1.0
            seen_keys.add(key)
    if stall >= WEDGE_LIMIT:
        r -= 1.0
    return r


def main():
    print(f"[reward] generic reward module for game={_bootstrap.GAME}. "
          f"step_reward() placeholder active; see TODO.")


if __name__ == "__main__":
    main()
