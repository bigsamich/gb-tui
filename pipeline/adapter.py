"""GameAdapter — the game-agnostic seam.

Adding a new Game Boy game = write ONE ``games/<game>/adapter.py`` that subclasses
``GameAdapter`` and drop in ``games/<game>/knowledge/``. Everything else in
``pipeline/`` (dataset build, LoRA distill, gguf packaging, autoplay/fleet, the CLI)
is generic and drives the game only through this interface.

The reference implementation is ``games/pokemon_red/adapter.py``, which delegates to
the existing ``executor`` / ``context`` / ``navigate`` modules. This ABC formalizes
the contract; the generic modules are NOT yet routed through it (that is a larger,
separate change) — the adapter is what a NEW game implements.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class GameAdapter(ABC):
    """RAM in -> structured state -> prompt blocks -> high-level action -> buttons.

    Concrete adapters set ``action_space`` and implement the required methods.
    ``abstract`` and ``reward`` are optional for now (used by ``play`` behavioral
    cloning and the generic reward signal respectively); default to NotImplemented.
    """

    #: high-level action verbs this game understands (e.g. walk_to/fight/flee/…)
    action_space: list[str] = []

    @abstractmethod
    def snapshot(self, raw_ram) -> dict:
        """Raw emulator RAM -> a structured state dict."""

    @abstractmethod
    def state_text(self, state: dict) -> str:
        """Render the STATE block shown to the model."""

    @abstractmethod
    def build_facts(self, state: dict) -> str:
        """Retrieved FACTS block (deterministic RAG over knowledge/)."""

    @abstractmethod
    def objective(self, state: dict, subgoals=None) -> str:
        """Current GOAL line (walkthrough-aware when subgoals are supplied)."""

    @abstractmethod
    def execute(self, action: dict, state: dict) -> str:
        """Run a high-level action against the emulator; return a short result tag."""

    # ---- optional / not-yet-required -------------------------------------

    def abstract(self, buttons: str, before: dict, after: dict) -> dict | None:
        """Buttons pressed -> the high-level action they represent (for ``play``
        behavioral cloning). Optional; return None if not derivable."""
        raise NotImplementedError

    def reward(self, before: dict, after: dict) -> float:
        """Per-step reward. Optional; the generic ``pipeline.reward`` is used when a
        game does not override this."""
        raise NotImplementedError
