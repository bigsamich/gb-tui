"""play_record — mode (1): YOU play the TUI; record state -> your action.

Behavioral-cloning teacher. Wraps the Rust gb-agent TUI, snapshots game state
before each of your inputs, and uses the active game's ``adapter.abstract()`` to
turn the raw buttons you pressed into a high-level action, emitting SFT examples
``(FACTS+STATE+GOAL -> action, think)`` into ``games/<game>/data/``.

TODO: implement TUI hook + per-input snapshot + adapter.abstract() labeling.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402  (sets up sys.path / game anchors)


def main():
    print(f"[play_record] STUB for game={_bootstrap.GAME}. Not implemented yet — "
          f"would record human TUI play into {_bootstrap.GAME_DIR / 'data'}.")


if __name__ == "__main__":
    main()
