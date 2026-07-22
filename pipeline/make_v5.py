"""Build the v5 dataset = v4 (fixed navigation + starter/dialog) + ERRAND demos.

v4 navigates and handles dialogs but can't reliably SEQUENCE the parcel errand (won't
leave Oak, picks off-map targets inside buildings). gen_errand_demos.py produces clean
(state + subgoal goal/hint -> correct high-level action) demos for the whole
deliver -> exit -> head-north sequence. v5 folds those in, weighted, on top of v4's
proven data. Held-out val/test stay = v4's so the navigation benchmark is comparable.

Usage: python3 make_v5.py
"""
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa

DATA = _bootstrap.GAME_DIR / "data"
DEMOS = _bootstrap.GAME_DIR / "data_demos"
rng = random.Random(42)


def load(p):
    return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]


def dedup(exs):
    seen, out = set(), []
    for x in exs:
        k = (x["messages"][1]["content"], x["messages"][2]["content"])
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def main():
    v4 = DATA / "v4"
    train = load(v4 / "train.jsonl")
    val = load(v4 / "val.jsonl")
    test = load(v4 / "test.jsonl")
    base_n = len(train)

    errand = dedup(load(DEMOS / "errand.jsonl")) if (DEMOS / "errand.jsonl").exists() else []
    # errand navigation is the whole point of v5 -> weight it strongly (5x)
    train = train + errand * 5
    rng.shuffle(train)

    out = DATA / "v5"
    out.mkdir(parents=True, exist_ok=True)
    for name, xs in [("train", train), ("val", val), ("test", test)]:
        with open(out / f"{name}.jsonl", "w") as f:
            for x in xs:
                f.write(json.dumps(x) + "\n")

    mix = Counter()
    for x in train:
        m = re.search(r'"action"\s*:\s*"(\w+)"', x["messages"][-1]["content"])
        mix[m.group(1) if m else "?"] += 1
    tot = sum(mix.values())
    stats = {
        "version": "v5", "base": "v4 train + errand demos x5",
        "train": len(train), "val": len(val), "test": len(test),
        "v4_base": base_n, "errand_unique": len(errand),
        "heal_pct": round(100 * mix.get("heal_at_center", 0) / tot, 2),
        "walk_pct": round(100 * mix.get("walk_to", 0) / tot, 2),
        "action_mix": dict(mix.most_common()),
    }
    (out / "stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1))
    assert stats["heal_pct"] < 3, "heal fraction too high -- would risk a collapse"
    assert len(errand) > 0, "no errand demos found -- run gen_errand_demos.py first"
    print("OK: v5 dataset written ->", out)


if __name__ == "__main__":
    main()
