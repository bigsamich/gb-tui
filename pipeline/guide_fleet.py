"""guide_fleet — mode (2): AI-teacher-corrected DAgger fleet (unattended).

The trained (small) model plays the fleet; at each decision the teacher label is
either cheap rule-based ground truth (type/matchup, HP-threshold, known warp) or,
where undecidable, a query to a STRONGER teacher model (``--teacher``). Student==teacher
-> confirmation; student!=teacher -> correction. Both are written as SFT examples into
``games/<game>/data/`` for the next ``distill``. Runs headless, no human in the loop.

TODO: generalize the pokemon_red-specific ``mine_autoplay`` teacher into an
adapter-driven rule set + optional LLM-teacher fallback, then drive the fleet.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402


def main():
    print(f"[guide_fleet] STUB for game={_bootstrap.GAME}. Not implemented yet — "
          f"would run a teacher-corrected DAgger fleet into "
          f"{_bootstrap.GAME_DIR / 'data'}.")


if __name__ == "__main__":
    main()
