"""Repo-root finder + sys.path setup for the game-agnostic pipeline.

Every moved module imports this at the top (via the tiny prelude that first puts
``<repo>/pipeline`` on ``sys.path`` so this file is importable):

    sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                                if (p / ".git").exists()) / "pipeline"))
    import _bootstrap  # noqa: E402

Importing it:
  - locates ``REPO_ROOT`` by walking up until a directory containing ``.git``
    (robust and depth-independent, so modules work from ``pipeline/`` OR
    ``games/<game>/`` without hard-coding ``parent.parent``),
  - reads the active game from ``$GBSKILL_GAME`` (default ``"pokemon_red"``),
  - prepends to ``sys.path``: ``<repo>/pipeline``, ``games/<game>``,
    ``games/<game>/synth`` — so flat ``import context`` / ``import autoplay`` /
    ``from synth import battle`` all keep resolving (game AND pipeline modules),
  - exposes ``REPO_ROOT``, ``GAME``, ``GAME_DIR``, ``KNOWLEDGE`` and ``RUN``.
"""

import os
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for p in (start, *start.parents):
        if (p / ".git").exists():
            return p
    # fall back to two-up (…/pipeline/_bootstrap.py -> repo root) if no .git found
    return start.parent.parent


REPO_ROOT = _find_repo_root(Path(__file__).resolve())
GAME = os.environ.get("GBSKILL_GAME", "pokemon_red")
GAME_DIR = REPO_ROOT / "games" / GAME
KNOWLEDGE = GAME_DIR / "knowledge"
RUN = REPO_ROOT / "run"

# Prepend the import roots so the flat `import context` / `import autoplay` style
# used throughout the pipeline keeps working from any file depth. Order matters:
# the only name that exists in BOTH pipeline/ and games/<game>/ is `adapter` (the
# ABC vs the game impl), so pipeline/ is put FIRST — a bare `import adapter` then
# canonically resolves to the pipeline ABC. The per-game adapter is loaded by file
# path, never by a bare `import adapter`.
for _p in (GAME_DIR / "synth", GAME_DIR, REPO_ROOT / "pipeline"):
    _s = str(_p)
    if _p.is_dir():
        if _s in sys.path:
            sys.path.remove(_s)
        sys.path.insert(0, _s)
