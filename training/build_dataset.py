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
    sets = {
        "battle": synth_battle.generate(),
        "nav": synth_nav.generate(),
        "meta": synth_meta.generate(),
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
    splits = {"test": all_ex[:n_test], "val": all_ex[n_test:n_test + n_val],
              "train": all_ex[n_test + n_val:]}

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
