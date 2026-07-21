"""Build dataset v2 = v1 + DAgger-style corrections from live rollouts.

Reads training/rollouts/*.jsonl (written by eval_live.py). For each logged
decision, computes the teacher-correct action where ground truth is derivable:
  - battle vs known enemy  -> best move (context.best_move)
  - wild PIKACHU + catch goal -> throw_ball
  - "Enter the Pokémon Center" in Cerulean -> walk_to(19,18) then up (door 19,17)
Model-correct decisions become confirmation examples; wrong ones become
corrections with a teacher think explaining the fix.

Usage: python3 make_v2.py           # -> data/v2 (v1 + corrections)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402
import context as C
import prompts

HERE = Path(__file__).resolve().parent
ROLL = _bootstrap.GAME_DIR / "rollouts"
V1 = _bootstrap.GAME_DIR / "data/v2base"
V2 = _bootstrap.GAME_DIR / "data/v2"


def teacher_action(rec):
    """-> (action dict, think str) or None if no ground truth derivable."""
    ctx, goal = rec.get("ctx", {}), rec.get("goal", "")
    snap = rec.get("snap", {})
    if ctx.get("in_battle"):
        enemy = ctx.get("enemy_species", "")
        if enemy == "PIKACHU" and "atch" in goal:
            return ({"action": "throw_ball"},
                    "This IS the wild Pikachu the goal demands — throw a ball before it gets hurt further.")
        our, moves = ctx.get("our_species", ""), ctx.get("our_moves", [])
        if our and moves:
            best, ranked = C.best_move(moves, our, enemy)
            if best and ranked and ranked[0][1] > 0:
                e = C.mon(enemy)
                m = C.move(best)
                x = C.type_multiplier(m["type"], e["types"]) if e else 1.0
                return ({"action": "fight", "move": best},
                        f"{enemy} is {'/'.join(e['types']) if e else '?'}; {best} scores highest "
                        f"({x}x{' +STAB' if m['type'] in (C.mon(our) or {}).get('types', []) else ''}).")
    else:
        if "Pokémon Center" in goal and ctx.get("map_name") == "CeruleanCity":
            return ({"action": "walk_to", "x": 19, "y": 17},
                    "The Cerulean Pokémon Center door is the warp at (19,17); walk onto it to enter.")
    return None


def build_corrections():
    out = []
    for f in sorted(ROLL.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            rec = json.loads(line)
            if "action" not in rec:      # summary line
                continue
            t = teacher_action(rec)
            if not t:
                continue
            correct, think = t
            model_act = rec["action"]
            kind = "rollout_confirm" if model_act == correct else "rollout_correction"
            ex = prompts.format_example(rec.get("facts", ""), rec["state_text"],
                                        rec["goal"], think, json.dumps(correct))
            ex["meta"] = {"kind": kind, "src": f.name, "model_action": model_act}
            out.append(ex)
    return out


def main():
    corrections = build_corrections()
    n_corr = sum(1 for x in corrections if x["meta"]["kind"] == "rollout_correction")
    print(f"rollout examples: {len(corrections)} ({n_corr} corrections, "
          f"{len(corrections) - n_corr} confirmations)")
    V2.mkdir(parents=True, exist_ok=True)
    for split in ("train", "val", "test"):
        rows = (V1 / f"{split}.jsonl").read_text().splitlines()
        if split == "train":
            # corrections are up-weighted 3x — they fix live mistakes
            rows = rows + [json.dumps(x) for x in corrections for _ in range(3)]
        (V2 / f"{split}.jsonl").write_text("\n".join(rows) + "\n")
        print(f"{split}: {len(rows)}")
    (V2 / "stats.json").write_text(json.dumps(
        {"base": "v1", "rollout_examples": len(corrections),
         "corrections": n_corr, "upweight": 3}, indent=1))


if __name__ == "__main__":
    main()
