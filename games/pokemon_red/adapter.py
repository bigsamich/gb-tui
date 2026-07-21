"""Pokémon Red adapter — the reference GameAdapter implementation.

Thin seam: it delegates to the modules that already encode the game knowledge and
emulator control (``executor`` for RAM snapshot / action execution and STATE text,
``context`` for the deterministic-RAG FACTS block, ``autoplay`` for the badge-derived
objective). This formalizes the interface a new game must satisfy; the generic
``pipeline/`` code keeps using ``executor``/``context``/``navigate`` directly for now.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402

import context as C  # noqa: E402
import executor as X  # noqa: E402
# _bootstrap orders pipeline/ ahead of this game dir on sys.path, so a bare
# ``import adapter`` resolves to the pipeline ABC (pipeline/adapter.py). This game
# adapter is itself loaded by file path (never via ``import adapter``).
from adapter import GameAdapter  # noqa: E402


class PokemonRed(GameAdapter):
    action_space = [
        "walk_to", "fight", "switch", "use_item", "throw_ball",
        "flee", "heal_at_center", "interact", "press", "done",
    ]

    def __init__(self, state_file):
        self.emu = X.Emu(state_file)

    def snapshot(self, raw_ram=None) -> dict:
        # Pokémon Red reads RAM live through the gb-agent CLI, so raw_ram is unused.
        return self.emu.snapshot()

    def state_text(self, state: dict) -> str:
        return X.state_text(state)

    def build_facts(self, state: dict) -> str:
        return C.build_facts(X.ctx_for(state))

    def objective(self, state: dict, subgoals=None) -> str:
        import autoplay  # deferred: pipeline module, brings the GYMS/objective logic
        past_gate = bool(state.get("badges")) or bool(
            {13, 51, 2} & {state.get("map")})
        has_parcel = autoplay.OAKS_PARCEL in state.get("bag", {})
        return autoplay.objective(state.get("badges", 0),
                                  has_pokedex=past_gate,
                                  has_parcel=has_parcel,
                                  has_party=bool(state.get("party")))

    def execute(self, action: dict, state: dict) -> str:
        return self.emu.do(action, state)


if __name__ == "__main__":
    a = PokemonRed(_bootstrap.REPO_ROOT / "run/bobby.state")
    print("action_space:", a.action_space)
    print("adapter OK (PokemonRed instantiated)")
