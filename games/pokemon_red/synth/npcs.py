"""Synthetic decisions for special one-off NPC offers (game knowledge the model
should know exists), e.g. the Magikarp salesman in the Route 4 / Mt Moon
Pokémon Center."""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402
import context as C
import prompts

rng = random.Random(31)

MAGIKARP_FACTS = ("A salesman here sells a MAGIKARP for $500. MAGIKARP is WATER, very "
                  "weak early (only Splash/Tackle), but evolves into GYARADOS (WATER/FLYING, "
                  "base atk 125 / hp 95) at level 20 — a top-tier attacker. Note: Gyarados is "
                  "4x weak to ELECTRIC and 2x to ROCK.")


# Water-type mons a party might already have (starter Squirtle line + catchables)
WATER_TYPES = {"SQUIRTLE", "WARTORTLE", "BLASTOISE", "MAGIKARP", "GYARADOS",
               "PSYDUCK", "GOLDUCK", "POLIWAG", "TENTACOOL", "STARYU", "SLOWPOKE"}


def gen_magikarp(n=300):
    out = []
    party_pools = [
        ["SQUIRTLE"], ["WARTORTLE"],                 # already have Water -> skip
        ["CHARMANDER"], ["CHARMELEON"],              # no Water -> buy
        ["BULBASAUR"], ["IVYSAUR"],
        ["CHARMELEON", "PIKACHU"], ["CHARMELEON", "SPEAROW"],
        ["WARTORTLE", "PIDGEY"],
    ]
    while len(out) < n:
        money = rng.choice([180, 350, 500, 500, 640, 900, 1200, 1746, 3300])
        party = rng.choice(party_pools)
        has_water = any(sp in WATER_TYPES for sp in party)
        pdesc = ", ".join(f"{sp} L{rng.randint(5,30)}" for sp in party)
        state = (f"In the Mt Moon / Route 4 Pokémon Center. A man offers to sell a MAGIKARP "
                 f"for $500. Money ${money}. Party: {pdesc}.")
        goal = "Build a team strong enough for the upcoming gyms."
        if money < 500:
            think = "Can't afford the $500 Magikarp — skip it, save the money for potions/balls."
            act, kind = {"action": "press", "buttons": "b:8 wait:60"}, "magikarp_skip_broke"
        elif len(party) >= 6:
            think = "Party is full; no room. Decline."
            act, kind = {"action": "press", "buttons": "b:8 wait:60"}, "magikarp_skip_full"
        elif has_water:
            wt = next(sp for sp in party if sp in WATER_TYPES)
            think = (f"I already have a Water type ({wt}) — likely the Squirtle line — so a $500 "
                     f"Magikarp that needs grinding to L20 is redundant. Skip it.")
            act, kind = {"action": "press", "buttons": "b:8 wait:60"}, "magikarp_skip_dupe"
        else:
            think = ("No Water type on the team (Charmander/Bulbasaur start). $500 is steep for a "
                     "weak Magikarp, but it evolves into GYARADOS (125 atk) at L20 — a strong "
                     "Water/Flying attacker and my best early Water option. Buy it.")
            act, kind = {"action": "interact"}, "magikarp_buy"
        ex = prompts.format_example(MAGIKARP_FACTS, state, goal, think, json.dumps(act))
        ex["meta"] = {"kind": kind}
        out.append(ex)
    return out


def generate():
    return gen_magikarp()


if __name__ == "__main__":
    xs = generate()
    print(len(xs), "npc examples")
    print(xs[0]["messages"][1]["content"][:300])
    print(xs[0]["messages"][2]["content"])
