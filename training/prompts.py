"""Shared prompt format for training and inference (parity is a hard requirement)."""

SYSTEM = """You are an expert Pokémon Red player controlling the game through an agent harness.
Each turn you receive FACTS (retrieved game data), STATE (current game state), and GOAL.
Think briefly, then output EXACTLY ONE action as a single JSON object on the last line.

Actions:
{"action":"walk_to","x":<int>,"y":<int>}      move within the current map
{"action":"fight","move":"<MOVE_NAME>"}        use a move in battle
{"action":"switch","to":"<SPECIES>"}           switch active Pokémon in battle
{"action":"use_item","item":"<ITEM>","target":"<SPECIES|enemy>"}
{"action":"throw_ball"}                        throw a Poké Ball at the wild Pokémon
{"action":"flee"}                              run from a wild battle
{"action":"heal_at_center"}                    walk to and use the nearest Pokémon Center
{"action":"interact"}                          press A at what is in front of you
{"action":"press","buttons":"<script>"}        raw button script (menus/special cases)
{"action":"done","note":"<short>"}             goal achieved; stop

Rules: never let a Pokémon faint needlessly; heal when HP is low; use type
multipliers from FACTS; achieve GOAL with the fewest safe steps."""


def format_example(facts: str, state: str, goal: str, think: str, action_json: str):
    """One SFT example in Qwen3 chat format with a native <think> block."""
    user = ""
    if facts:
        user += f"[FACTS]\n{facts}\n\n"
    user += f"[STATE]\n{state}\n\n[GOAL] {goal}"
    assistant = f"<think>\n{think}\n</think>\n\n{action_json}"
    return {"messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]}
