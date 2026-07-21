"""learn_selfplay — mode (3): reward-filtered self-imitation fleet.

Hands-off grinding once a plan exists: the model self-plays from the walkthrough
subgoals, ``reward.py`` scores each trajectory, and high-reward decisions are kept as
SFT examples ``(FACTS+STATE+GOAL -> action, think)`` in ``games/<game>/data/``. Loop
``learn -> distill -> learn`` to self-improve.

TODO: implement self-play rollout loop + reward filtering + example emission.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402


def main():
    print(f"[learn_selfplay] STUB for game={_bootstrap.GAME}. Not implemented yet — "
          f"would run a reward-filtered self-imitation fleet into "
          f"{_bootstrap.GAME_DIR / 'data'}.")


if __name__ == "__main__":
    main()
