"""Synthetic teacher decisions distilled from playing the Cerulean (Misty) gym
by hand. These bake the transferable skills the model should have — the user's
ask: "party switching should be baked into our pipeline; things you do should be
captured." Each example is one (facts, state, goal) -> (think, action).

Skills captured:
  - Water gym -> Electric (ThunderShock 2x) is the answer; Fire (Ember) is 0.5x.
  - Party ORDER / SWITCHING for type advantage and to protect a fragile sweeper.
  - Tank the chip damage with the bulky mon, save the 2x sweeper for the boss.
  - Buy Potions before a hard gym; HEAL before a crit can KO (gen-1 water mons
    crit often — high base speed).
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402
import context as C
import prompts

rng = random.Random(65)

MISTY_FACTS = (
    "MISTY (Cerulean Gym, WATER): STARYU L18 then STARMIE L21 (fast, Bubblebeam). "
    "The gym also has water trainers (Horsea, Shellder, Goldeen). WATER is 2x weak to "
    "ELECTRIC and GRASS; resists FIRE/WATER/ICE (0.5x). ThunderShock is 2x on every mon "
    "here. Gen-1 note: Staryu/Starmie have high base speed = high CRIT rate, so their "
    "Bubblebeam can spike ~2x damage — heal before you get low enough to be KO'd."
)


def _ex(facts, state, goal, think, action, kind):
    ex = prompts.format_example(facts, state, goal, think, json.dumps(action))
    ex["meta"] = {"kind": kind}
    return ex


def gen():
    out = []

    # 1) In battle vs a Misty mon with Pikachu out -> ThunderShock (2x).
    for enemy in ("STARYU", "STARMIE"):
        state = (f"In battle vs Misty. Enemy {enemy} (WATER). Active PIKACHU L18 44/44 "
                 f"with THUNDERSHOCK, GROWL, THUNDER_WAVE, QUICK_ATTACK. Party also has "
                 f"CHARMELEON L32.")
        think = (f"{enemy} is WATER, so THUNDERSHOCK is 2x super-effective and my best "
                 f"damage — far better than Charmeleon's Ember (0.5x, resisted). Attack.")
        out.append(_ex(MISTY_FACTS, state, "Beat Misty for the Cascade Badge.",
                       think, {"action": "fight", "move": "THUNDERSHOCK"}, "misty_pikachu_ts"))

    # 2) In battle vs a Misty/water mon with CHARMELEON out but PIKACHU healthy on
    #    bench -> SWITCH to Pikachu (the 2x sweeper), eating the switch hit on the tank.
    state = ("In battle vs Misty. Enemy STARMIE L21 (WATER). Active CHARMELEON L32 with "
             "RAGE, GROWL, EMBER, LEER (Ember is 0.5x here). Bench: PIKACHU L18 44/44.")
    think = ("Charmeleon's only attack that isn't resisted is neutral at best; against a "
             "WATER boss my 2x answer is Pikachu's ThunderShock. Switch to PIKACHU — the "
             "enemy hits the incoming mon, but the electric 2x pays for it.")
    out.append(_ex(MISTY_FACTS, state, "Beat Misty for the Cascade Badge.",
                   think, {"action": "switch", "to": "PIKACHU"}, "misty_switch_to_pika"))

    # 3) TANK: a water TRAINER (not the boss) with Pikachu fragile -> lead/keep the
    #    bulky Charmeleon in to soak chip so Pikachu stays full for Misty.
    state = ("In battle vs a gym trainer. Enemy GOLDEEN L19 (WATER). Active CHARMELEON "
             "L32 93/93. Bench: PIKACHU L18 44/44 (need it full for Misty).")
    think = ("This is a throwaway trainer, not the boss. Charmeleon (93 HP) can tank it "
             "with Ember; I must NOT spend Pikachu's 44 HP here — keep it benched and full "
             "so it can 2x-sweep Staryu/Starmie later. Attack with Charmeleon.")
    out.append(_ex(MISTY_FACTS, state, "Clear the gym trainers, then beat Misty.",
                   think, {"action": "fight", "move": "EMBER"}, "misty_tank_trainer"))

    # 4) HEAL before a crit can KO: low-ish HP + Potions in bag -> use a Potion.
    for hp, mx in ((24, 93), (18, 44), (30, 93)):
        who = "CHARMELEON" if mx == 93 else "PIKACHU"
        state = (f"In battle vs Misty. Enemy STARMIE L21 (fast, Bubblebeam can crit). "
                 f"Active {who} {hp}/{mx}. Bag: POTION x9.")
        think = (f"{who} is at {hp}/{mx} ({100*hp//mx}%). Starmie crits often and a crit "
                 f"Bubblebeam could KO me from here. Heal now with a Potion rather than "
                 f"trading — I have plenty.")
        out.append(_ex(MISTY_FACTS, state, "Beat Misty for the Cascade Badge.",
                       think, {"action": "use_item", "item": "POTION", "target": who},
                       "misty_heal_low"))

    # 5) PREP: about to challenge a hard water gym, low on healing -> buy Potions.
    state = ("Standing in Cerulean City with $3356. Team: CHARMELEON L32, PIKACHU L18. "
             "Bag has only 1 Potion. Misty's gym is next.")
    think = ("Misty's Staryu/Starmie crit hard; one Potion won't cover a crit-heavy fight. "
             "With $3356 I can afford a stack of Potions at the Mart to heal through her "
             "damage. Buy Potions before challenging the gym.")
    out.append(_ex(MISTY_FACTS, state, "Beat Misty for the Cascade Badge.",
                   think, {"action": "press", "buttons": "walk_to_mart_buy_potions"},
                   "misty_prep_buy"))

    return out


def generate():
    return gen()


if __name__ == "__main__":
    xs = generate()
    print(len(xs), "gym-strategy examples")
    for x in xs[:2]:
        print("---")
        print(x["messages"][1]["content"][:200])
        print(x["messages"][2]["content"])
