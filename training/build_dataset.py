"""Assemble the versioned SFT dataset: synthetic + mined journals.

Usage: python3 build_dataset.py [version]   (default: auto-increment vN)
Writes training/data/vN/{train,val,test}.jsonl + stats.json
"""

import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "synth"))

import mine_journals
from synth import battle as synth_battle  # noqa: E402
from synth import meta as synth_meta
from synth import nav as synth_nav
from synth import npcs as synth_npcs
from synth import gym_strategy as synth_gym

DATA = Path(__file__).resolve().parent / "data"
rng = random.Random(42)


def contradiction(ex) -> bool:
    """Drop mined examples that contradict the catch policy without context
    (e.g. fleeing a wild Pikachu when the goal says to catch one — those were
    DSum-timing flees that need context we can't reconstruct)."""
    if ex["meta"].get("kind") != "journal":
        return False
    user = ex["messages"][1]["content"]
    asst = ex["messages"][2]["content"]
    return "PIKACHU" in user.split("[STATE]")[-1] and '"flee"' in asst and "atch" in user


def main():
    import mine_autoplay
    import json as _json

    def _dedup_cap(exs, cap):
        """Collapse near-identical examples (stuck runs repeat the same state->action
        thousands of times) by (user,assistant) content, then cap per KIND so no
        single correction type drowns out the rest."""
        seen, uniq = set(), []
        for x in exs:
            key = (x["messages"][1]["content"], x["messages"][2]["content"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(x)
        by_kind, kept = {}, []
        rng.shuffle(uniq)
        for x in uniq:
            k = x["meta"].get("kind", "?")
            by_kind[k] = by_kind.get(k, 0) + 1
            if by_kind[k] <= cap:
                kept.append(x)
        return kept

    # DAgger corrections mined from the fleet's own play, DEDUPED + capped per kind
    # (the 83k harvest is mostly repeated stuck-state duplicates). Corrections 3x.
    auto = _dedup_cap(mine_autoplay.mine(), cap=1500)
    auto_weighted = []
    for x in auto:
        auto_weighted.extend([x] * (3 if x["meta"]["kind"].endswith("fix") else 1))

    # Hand-guided STARTER demonstrations (choose -> take -> rival -> exit), deduped
    # (the choose/take decisions are the few unique, high-value ones), 3x-weighted.
    demo_f = Path(__file__).resolve().parent / "data_demos" / "starter.jsonl"
    demo_ex = [_json.loads(l) for l in demo_f.read_text().splitlines() if l.strip()] \
        if demo_f.exists() else []
    demo_ex = _dedup_cap(demo_ex, cap=9999)
    auto_weighted += demo_ex * 3
    print(f"autoplay corrections (deduped+capped, weighted): {len(auto_weighted) - len(demo_ex)*3} | "
          f"starter demos (deduped, 3x): {len(demo_ex)} -> {len(demo_ex)*3}")

    sets = {
        "battle": synth_battle.generate(),
        "nav": synth_nav.generate(),
        "meta": synth_meta.generate(),
        "npc": synth_npcs.generate(),
        "gym": synth_gym.generate(),
        "journal": mine_journals.mine(),
    }
    all_ex = []
    for name, xs in sets.items():
        xs = [x for x in xs if not contradiction(x)]
        print(f"{name}: {len(xs)}")
        all_ex.extend(xs)
    rng.shuffle(all_ex)

    n = len(all_ex)
    n_val, n_test = max(50, n // 40), max(50, n // 40)
    # Fleet DAgger corrections go to TRAIN ONLY — never test/val — so the held-out
    # benchmark stays clean and comparable across versions (no leakage from the 3x dupes).
    print(f"autoplay(train-only, 3x-weighted corrections): {len(auto_weighted)}")
    splits = {"test": all_ex[:n_test], "val": all_ex[n_test:n_test + n_val],
              "train": all_ex[n_test + n_val:] + auto_weighted}
    rng.shuffle(splits["train"])

    if len(sys.argv) > 1:
        ver = sys.argv[1]
    else:
        existing = sorted(int(p.name[1:]) for p in DATA.glob("v*") if p.name[1:].isdigit())
        ver = f"v{(existing[-1] + 1) if existing else 1}"
    out = DATA / ver
    out.mkdir(parents=True, exist_ok=True)
    for split, xs in splits.items():
        with open(out / f"{split}.jsonl", "w") as f:
            for x in xs:
                f.write(json.dumps(x) + "\n")
    stats = {
        "version": ver, "total": n,
        "splits": {k: len(v) for k, v in splits.items()},
        "kinds": dict(Counter(x["meta"]["kind"] for x in all_ex)),
        "approx_tokens_per_example": sum(len(m["content"]) for m in all_ex[0]["messages"]) // 4,
    }
    (out / "stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
