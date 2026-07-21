"""Mine the play journals (journal/session-*/events.jsonl) into training examples.

Keeps decision events with outcome "Done" whose Rust-Debug action string parses
into the harness action vocabulary. STATE/FACTS are rebuilt with the shared
context builder so mined examples match synthetic ones exactly.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402
import context as C
import prompts

ROOT = _bootstrap.REPO_ROOT


def parse_action(a: str):
    """Rust Debug enum -> action JSON dict (None if unknown)."""
    a = a.strip()
    m = re.match(r'WalkTo\((\d+),\s*(\d+)\)', a)
    if m:
        return {"action": "walk_to", "x": int(m.group(1)), "y": int(m.group(2))}
    m = re.match(r'Press\("([^"]*)"\)', a)
    if m:
        return {"action": "press", "buttons": m.group(1)}
    if a.startswith("Fight"):
        m = re.search(r'"([^"]+)"', a)
        return {"action": "fight", **({"move": m.group(1).upper().replace(" ", "_")} if m else {})}
    if a.startswith("Flee"):
        return {"action": "flee"}
    if a.startswith("Interact"):
        return {"action": "interact"}
    if a.startswith("HealAtCenter"):
        return {"action": "heal_at_center"}
    if a.startswith("UseItem"):
        ms = re.findall(r'"([^"]+)"', a)
        return {"action": "use_item", "item": (ms[0] if ms else "?")}
    return None


def norm(s):
    return (s or "").upper().replace(" ", "_").replace("♀", "_F").replace("♂", "_M")


def state_text(gs: dict) -> tuple[str, dict]:
    if not gs:
        return "", {}
    party = gs.get("party") or []
    mapn = (gs.get("map_name") or f"map{gs.get('map')}").replace(" ", "")
    battle = gs.get("battle")
    ctx = {"map_name": mapn,
           "party": [{"species": norm(p.get("name")), "level": p.get("level", 0)} for p in party[:2]]}
    if battle:
        enemy = norm(battle.get("enemy_name"))
        bits = [f"In battle ({'wild' if battle.get('kind')==1 else 'trainer'}). "
                f"Enemy {enemy} L{battle.get('enemy_level','?')} HP {battle.get('enemy_hp','?')}."]
        ctx["in_battle"] = True
        ctx["enemy_species"] = enemy
        if party:
            ctx["our_species"] = norm(party[0].get("name"))
            ctx["our_moves"] = [norm(m.get("name")) for m in party[0].get("moves", [])]
    else:
        bits = [f"Overworld on map {mapn} at ({gs.get('x')},{gs.get('y')})."]
    for p in party:
        mv = "/".join(f"{norm(m.get('name'))}({m.get('pp')})" for m in p.get("moves", []))
        bits.append(f"{norm(p.get('name'))} L{p.get('level','?')} HP {p.get('hp','?')}/{p.get('max_hp','?')} [{mv}]")
    if gs.get("money") is not None:
        bits.append(f"Money ${gs['money']}. Badges {gs.get('badges',0)}.")
    return " ".join(bits), ctx


THINK = {
    "flee": "This wild fight doesn't serve the goal — flee to save HP and PP.",
    "fight": "Fighting is the right call here; use the strongest usable move.",
    "walk_to": "Moving toward the objective location.",
    "press": "A scripted input sequence is needed for this menu/dialog state.",
    "interact": "Interacting with the object/NPC ahead advances the goal.",
    "heal_at_center": "HP/PP are low enough that healing comes first.",
    "use_item": "Using the item is the efficient play here.",
}


def mine():
    out, seen = [], set()
    for f in sorted(ROOT.glob("journal/session-*/events.jsonl")):
        for line in f.read_text().splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") != "decision" or ev.get("outcome") != "Done":
                continue
            aj = parse_action(ev.get("action", ""))
            if not aj:
                continue
            st, ctx = state_text(ev.get("state"))
            if not st:
                continue
            key = (st[:160], json.dumps(aj, sort_keys=True))
            if key in seen:      # journals repeat states heavily (flee loops)
                continue
            seen.add(key)
            facts = C.build_facts(ctx)
            goal = (ev.get("goal") or "Continue the playthrough.").strip()[:200]
            think = THINK.get(aj["action"], "Advancing the goal.")
            ex = prompts.format_example(facts, st, goal, think, json.dumps(aj))
            ex["meta"] = {"kind": "journal", "src": f.parent.name}
            out.append(ex)
    return out


if __name__ == "__main__":
    xs = mine()
    print(len(xs), "journal examples (deduped)")
    if xs:
        print(xs[0]["messages"][1]["content"][:400])
        print(xs[0]["messages"][2]["content"][:200])
