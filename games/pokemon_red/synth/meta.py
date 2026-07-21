"""Battle-meta and recovery decisions: heal timing, switches, PP management,
and the documented failure->recovery patterns from the play journals."""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402
import context as C
import prompts

rng = random.Random(23)


def gen_heal(n=250):
    out = []
    while len(out) < n:
        sp, lvl, mx = rng.choice([("CHARMELEON", 30, 87), ("PIKACHU", 15, 40), ("CHARIZARD", 40, 120)])
        hp = rng.randint(1, mx)
        frac = hp / mx
        state = (f"Overworld on map Route4 at ({rng.randint(10,80)},{rng.randint(4,14)}). "
                 f"Party: {sp} L{lvl} HP {hp}/{mx}. No battle.")
        goal = "Grind levels safely; never black out."
        if frac < 0.3:
            think = f"{hp}/{mx} is under 30% — one bad encounter could faint us. Heal before continuing; blackouts lose money and progress."
            act = json.dumps({"action": "heal_at_center"})
            kind = "heal_yes"
        else:
            think = f"{hp}/{mx} is comfortable; a heal trip now wastes time. Keep grinding."
            act = json.dumps({"action": "walk_to", "x": 70, "y": 12})
            kind = "heal_no"
        ex = prompts.format_example("", state, goal, think, act)
        ex["meta"] = {"kind": kind}
        out.append(ex)
    return out


def gen_switch_protect(n=250):
    """The run's core trick: weak lead gets XP credit, then switch to the carry."""
    out = []
    while len(out) < n:
        lead_lvl = rng.randint(5, 14)
        enemy = rng.choice(["PIDGEOTTO", "CLEFAIRY", "ONIX", "MANKEY", "STARYU", "ZUBAT", "GEODUDE"])
        elvl = rng.randint(14, 21)
        e = C.mon(enemy)
        state = (f"In battle (trainer). Our PIKACHU L{lead_lvl} HP {rng.randint(10,30)}/30 is out. "
                 f"Enemy {enemy} L{elvl}. Party also has CHARMELEON L30 HP 80/87.")
        goal = "Level up Pikachu WITHOUT letting it battle; it must not faint."
        think = (f"Pikachu L{lead_lvl} cannot safely fight {enemy} L{elvl}. It already earned XP share by "
                 f"being sent out — switch to Charmeleon to do the fighting.")
        act = json.dumps({"action": "switch", "to": "CHARMELEON"})
        ex = prompts.format_example(C.battle_facts(enemy, "PIKACHU", ["THUNDERSHOCK", "GROWL"]),
                                    state, goal, think, act)
        ex["meta"] = {"kind": "switch_protect"}
        out.append(ex)
    return out


def gen_pp_recovery(n=150):
    """Documented failure: selecting a 0-PP move loops the menu. Recover by
    choosing a move with PP, or fleeing when nothing usable remains."""
    out = []
    while len(out) < n:
        pps = {"RAGE": 0, "GROWL": rng.randint(10, 39), "EMBER": rng.choice([0, rng.randint(1, 24)]),
               "LEER": rng.randint(0, 20)}
        enemy = rng.choice(["ZUBAT", "PARAS", "GEODUDE", "CLEFAIRY"])
        state = (f"In battle (wild). Our CHARMELEON L29 HP {rng.randint(20,70)}/84. "
                 f"Move PP: " + ", ".join(f"{m} {p}" for m, p in pps.items()) +
                 f". Enemy {enemy} L{rng.randint(8,12)}.")
        goal = "Get through the cave; conserve resources."
        if pps["EMBER"] > 0:
            think = "Rage has 0 PP — selecting it just errors and loops the menu. Ember still has PP; use it."
            act = json.dumps({"action": "fight", "move": "EMBER"})
            kind = "pp_pick_usable"
        else:
            think = "Both damaging moves are at 0 PP; only status moves remain. Fighting is pointless — flee and go restore PP."
            act = json.dumps({"action": "flee"})
            kind = "pp_flee"
        ex = prompts.format_example(C.battle_facts(enemy, "CHARMELEON", list(pps)), state, goal, think, act)
        ex["meta"] = {"kind": kind}
        out.append(ex)
    return out


def gen_menu_recovery(n=150):
    """Dialog/menu traps we hit: NPC dialog freezing movement, wrong menu."""
    out = []
    scenarios = [
        ("A text box is open (an NPC dialog); movement is ignored.",
         "Dialog boxes block walking. Advance/close with B — never A near NPCs, which can re-open the dialog.",
         {"action": "press", "buttons": "b:8 wait:60 b:8 wait:60"}),
        ("The battle ITEM bag opened by mistake ('This isn't yours to use!').",
         "Wrong menu — close the bag with B and return to FIGHT.",
         {"action": "press", "buttons": "b:8 wait:40 b:8 wait:40"}),
        ("Standing on the Pokémon Center exit mat but not exiting; nurse dialog re-opens on A.",
         "Clear the lingering dialog with a single B, take one turning step, then walk down out the door.",
         {"action": "press", "buttons": "b:8 wait:80 down:2 wait:20 down:16 wait:20 down:16 wait:20"}),
    ]
    while len(out) < n:
        desc, think, act = rng.choice(scenarios)
        state = f"Overworld-ish stuck state: {desc} Position unchanged for 3 attempts."
        ex = prompts.format_example("", state, "Continue the current journey.", think, json.dumps(act))
        ex["meta"] = {"kind": "menu_recovery"}
        out.append(ex)
    return out


def generate():
    return gen_heal() + gen_switch_protect() + gen_pp_recovery() + gen_menu_recovery()


if __name__ == "__main__":
    xs = generate()
    print(len(xs), "meta examples")
    print(xs[0]["messages"][2]["content"])
