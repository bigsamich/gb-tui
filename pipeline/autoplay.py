"""Continuous, long-horizon AUTONOMOUS play — the MODEL decides every step.

This is the real deliverable: the trained model plays Pokémon Red on its own and
progresses through the game. Unlike eval_live.py (short fixed scenarios), this runs
an open-ended loop from a start state toward a badge-derived objective, logging
every decision for DAgger and tracking how far the model actually gets.

Usage:
  python3 autoplay.py --model pokered-8b-v2 --state run/start-charmander.state \
      --steps 1500 --tag charmander-brock
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402
import context as C
import executor as X
import prompts
from serve_shim import ask, extract_action
try:                                    # optional per-game dialog teacher (guide mode)
    import dialog_teacher as DT
except ImportError:
    DT = None
try:                                    # optional walkthrough-derived subgoal spine
    import subgoals as SG
except ImportError:
    SG = None

ROOT = _bootstrap.REPO_ROOT
RUNS = _bootstrap.GAME_DIR / "autoplay_runs"
RUNS.mkdir(exist_ok=True)

GYMS = [  # (badge bit, city, one-line objective)
    (0x01, "Pewter",    "Head north/west through Viridian and Viridian Forest to PEWTER CITY, then beat BROCK (Rock gym) for the Boulder Badge."),
    (0x02, "Cerulean",  "Go east through Mt Moon to CERULEAN CITY and beat MISTY (Water gym) for the Cascade Badge."),
    (0x04, "Vermilion", "Go south to VERMILION CITY (Cut the tree / S.S. Anne) and beat LT. SURGE (Electric gym) for the Thunder Badge."),
    (0x08, "Celadon",   "Head west to CELADON CITY and beat ERIKA (Grass gym) for the Rainbow Badge."),
    (0x10, "Fuchsia",   "Reach FUCHSIA CITY and beat KOGA (Poison gym) for the Soul Badge."),
    (0x20, "Saffron",   "Reach SAFFRON CITY and beat SABRINA (Psychic gym) for the Marsh Badge."),
    (0x40, "Cinnabar",  "Reach CINNABAR ISLAND and beat BLAINE (Fire gym) for the Volcano Badge."),
    (0x80, "Viridian",  "Return to VIRIDIAN CITY gym and beat GIOVANNI for the Earth Badge."),
]


OAKS_PARCEL = 70

ERRAND_GET = ("EARLY-GAME GATE: a man sleeping in north Viridian City blocks the exit to "
              "Route 2 until you have the POKEDEX. Step 1: go to the VIRIDIAN POKE MART "
              "and talk to the CLERK behind the counter to receive OAK'S PARCEL. ")
ERRAND_DELIVER = ("You are HOLDING OAK'S PARCEL. Step 2: leave this building by its Exit, "
                  "travel SOUTH back to PALLET TOWN, enter OAK'S LAB, and talk to PROF. OAK "
                  "to deliver the parcel — he gives you the POKEDEX, which clears the "
                  "Viridian gate so you can head north. ")


STARTER_FACTS = (
    "You have NO Pokémon yet. On the table in OAK'S LAB are three starter Poké Balls: "
    "CHARMANDER (Fire) at (6,3), SQUIRTLE (Water) at (7,3), BULBASAUR (Grass) at (8,3). "
    "Walk directly below a ball and INTERACT (press A facing it) to take that Pokémon as "
    "your starter. After you choose, your RIVAL grabs the type-advantage starter and battles "
    "you immediately — beat him with your new Pokémon's attack move.")


def objective(badges: int, has_pokedex: bool = True, has_parcel: bool = False,
              has_party: bool = True) -> str:
    if not has_party:
        return ("Get your FIRST Pokémon: choose a starter from the three Poké Balls in Oak's "
                "Lab, then win the rival battle that follows.")
    for bit, _city, obj in GYMS:
        if not (badges & bit):
            if bit == 0x01 and not has_pokedex:
                return (ERRAND_DELIVER if has_parcel else ERRAND_GET) + obj
            return obj
    return "All 8 badges — head to Victory Road and the Elite Four."


def run(state_path: str, model: str, url: str, steps: int, tag: str):
    stamp = tag or Path(state_path).stem
    emu = X.Emu(state_path)
    log_path = RUNS / f"{model.replace(':','_')}-{stamp}.jsonl"
    log = log_path.open("w")

    s0 = emu.snapshot()
    print(f"AUTOPLAY start: model={model} state={state_path}", flush=True)
    print(f"  map={s0['map']} pos=({s0['x']},{s0['y']}) badges={bin(s0['badges'])} "
          f"party={[(p['species'], p['level']) for p in s0['party']]}", flush=True)

    seen_maps = {s0["map"]}
    start_badges = s0["badges"]
    last_key = None
    stall = 0
    dlg_last = None       # last autopilot screen text (stall-guard)
    dlg_reps = 0          # consecutive autopilot presses on the same screen
    # walkthrough subgoal index -- PERSISTED to disk so a watchdog restart resumes the
    # right subgoal (else a run that already delivered the parcel would regress to
    # "get parcel", since that done-condition (has item 70) reverts after delivery).
    sgidx_path = ROOT / "run" / f"{stamp}.sgidx"
    sg_idx = int(sgidx_path.read_text()) if sgidx_path.exists() else 0
    for step in range(steps):
        s = emu.snapshot()
        seen_maps.add(s["map"])
        # past the Viridian gate once we've reached Route 2 (13) / Viridian Forest (51) / Pewter (2)
        past_gate = bool(seen_maps & {13, 51, 2}) or s["badges"]
        has_parcel = OAKS_PARCEL in s.get("bag", {})
        has_party = bool(s.get("party"))
        # WALKTHROUGH SPINE: the current subgoal is the model's directed objective. This
        # is the fix for long-horizon progression -- the fleet could navigate but not
        # sequence the game (got the parcel, then wandered instead of delivering it).
        sg_hint = ""
        if SG is not None:
            new_idx = SG.advance(sg_idx, s)
            if new_idx != sg_idx:
                sgidx_path.write_text(str(new_idx))   # persist progress across restarts
            sg_idx = new_idx
            sg = SG.current(sg_idx)
            if sg:
                goal = sg["objective"]
                sg_hint = SG.hint_for(sg, s)
            else:
                goal = objective(s["badges"], has_pokedex=bool(past_gate),
                                 has_parcel=has_parcel, has_party=has_party)
        else:
            goal = objective(s["badges"], has_pokedex=bool(past_gate),
                             has_parcel=has_parcel, has_party=has_party)

        # progress heartbeat
        if s["badges"] != start_badges:
            print(f"  *** BADGE GAINED at step {step}: {bin(s['badges'])} ***", flush=True)
            start_badges = s["badges"]
            emu.run("")  # no-op; checkpoint below
            import shutil
            shutil.copy(state_path, RUNS / f"{stamp}-badges{s['badges']}.state")

        st, ctx = X.state_text(s), X.ctx_for(s)
        facts = C.build_facts(ctx)
        if sg_hint:                             # grounded how-to for the current subgoal
            facts = f"GUIDE: {sg_hint}" + ("\n" + facts if facts else "")
        if not has_party:                       # perception of the starter balls
            facts = STARTER_FACTS + ("\n" + facts if facts else "")

        # DIALOG AUTOPILOT (guide mode): a non-battle text box / menu is not an overworld
        # strategy decision -- it just needs the button the SCREEN calls for. The dialog
        # TEACHER supplies that button as ground truth (reads perception, not blind timing).
        # We apply it, log it as a SCREEN->button training example, and continue WITHOUT a
        # model query. This keeps runs PROGRESSING through dialogs and mints clean
        # perception-labeled data for the v4 distill -- the whole point of this restart.
        if DT is not None and not s["in_battle"] and s.get("screen_text") \
                and any(c.isalpha() for c in s["screen_text"]):
            lesson = DT.teach(s["screen_text"], s.get("screen_menu", False), goal)
            # Stall guard: if the same screen persists after several autopilot presses,
            # the teacher button isn't advancing it (e.g. Oak's "which do you want?" which
            # needs a WALK, not A) -- hand control back to the model to break the loop.
            same = s["screen_text"] == dlg_last
            dlg_reps = dlg_reps + 1 if same else 0
            dlg_last = s["screen_text"]
            if lesson and dlg_reps < 4:
                btn, think = lesson
                act = {"action": "press", "buttons": f"{btn}:8 wait:60"}
                emu.run(f"{btn}:8 wait:90")
                log.write(json.dumps({"step": step, "goal": goal, "facts": facts,
                    "state_text": st, "ctx": ctx, "action": act, "exec": "dialog-teacher",
                    "think": think, "snap": s, "mode": "dialog_teacher"}) + "\n")
                log.flush()
                continue
        # stall hint: if wedged, tell the model it's stuck so it varies its action
        key = (s["map"], s["x"], s["y"], s["in_battle"])
        stall = stall + 1 if key == last_key else 0
        last_key = key
        hint = ""
        if stall >= 6:
            hint = ("\n[NOTE] You have not moved for several turns — you are stuck against "
                    "something. Try a DIFFERENT direction or a map exit/warp.")
        user = (f"[FACTS]\n{facts}\n\n" if facts else "") + \
               f"[STATE]\n{st}{hint}\n\n[GOAL] {goal}"
        msgs = [{"role": "system", "content": prompts.SYSTEM},
                {"role": "user", "content": user}]
        try:
            raw = ask(model, msgs, url)
        except Exception as ex:
            raw = ""
            print(f"  [step {step}] model error: {ex}", flush=True)
        act = extract_action(raw) or {"action": "press", "buttons": "b:8 wait:60"}
        # "Walk to where I already am" = the model wants to ENGAGE what's here (e.g. it
        # navigated to Oak at (5,4) but keeps re-issuing walk_to instead of interacting).
        # Convert a redundant self-targeted walk_to into an interact.
        if act.get("action") == "walk_to" and (act.get("x"), act.get("y")) == (s["x"], s["y"]):
            act = {"action": "interact"}
        res = emu.do(act, s)
        # Log the FULL structured snapshot (incl. party hp/moves/pp and, in battle,
        # enemy species/hp/level) so DAgger mining can derive teacher labels
        # (heal-when-low, best-move, catch-rare) from clean fields, not regex on text.
        log.write(json.dumps({"step": step, "goal": goal, "facts": facts,
                              "state_text": st, "ctx": ctx, "action": act, "exec": res,
                              "snap": s, "stall": stall}) + "\n")
        log.flush()
        if step % 25 == 0:
            print(f"  step {step}: map={s['map']} pos=({s['x']},{s['y']}) "
                  f"badges={bin(s['badges'])} battle={s['in_battle']} maps={len(seen_maps)} "
                  f"act={act.get('action')}", flush=True)
        if step % 100 == 0 and step:
            import shutil
            shutil.copy(state_path, RUNS / f"{stamp}-latest.state")

    final = emu.snapshot()
    print(f"AUTOPLAY done: {steps} steps. final map={final['map']} "
          f"badges={bin(final['badges'])} maps_seen={len(seen_maps)} "
          f"party={[(p['species'], p['level']) for p in final['party']]}", flush=True)
    log.write(json.dumps({"result": "done", "steps": steps,
                          "final": {k: v for k, v in final.items() if k != 'party'},
                          "maps_seen": sorted(seen_maps)}) + "\n")
    log.close()
    return final


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="pokered-8b-v2")
    ap.add_argument("--state", default="run/start-charmander.state")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--steps", type=int, default=1500)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    run(str(ROOT / args.state) if not args.state.startswith("/") else args.state,
        args.model, args.url, args.steps, args.tag)
