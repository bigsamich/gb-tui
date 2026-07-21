"""walkthrough_ingest — turn a user-provided guide file into subgoals.json.

Reads a LOCAL walkthrough file (never scrapes) and uses an LLM to distill it into
a grounded checklist ``games/<game>/subgoals.json`` of
``{objective, trigger_location, done_condition}`` items — STRUCTURE only, not verbatim
prose. Prefer CC-licensed sources if anything is committed/attributed; proprietary
guides stay local inference-time only and are never trained into published weights.

TODO: implement guide parsing + LLM distillation -> subgoals.json.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402


def main():
    print(f"[walkthrough_ingest] STUB for game={_bootstrap.GAME}. Not implemented yet "
          f"— would distill a local guide into {_bootstrap.GAME_DIR / 'subgoals.json'}.")


if __name__ == "__main__":
    main()
