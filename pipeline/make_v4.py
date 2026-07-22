"""Build the v4 dataset = v2's PROVEN-GOOD data + the new-framework skills only.

Why not re-run build_dataset: v3 regressed (collapsed to heal_at_center on every
overworld-with-party state). Diagnosis: v3's heal_fix DAgger mining exploded heal from
v2's 76 examples (1%) to 697 (7%, 9x), tipping the model into a heal-attractor. v2
navigates perfectly. So v4 starts from v2's EXACT training data (heal balance intact) and
adds ONLY the additive skills we're confident about:
  * starter demos  (choose -> take -> rival -> exit)          x3
  * dialog demos   (perception-grounded SCREEN -> button)     x4   [new framework]
No re-mining, no gym-synth churn -- nothing that could re-inflate heal. Held-out val/test
stay = v2's so the navigation benchmark is directly comparable (regression gate).

Usage: python3 make_v4.py
"""
import json
import random
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


def action_mix(exs):
    import re
    c = Counter()
    for x in exs:
        a = x["messages"][-1]["content"]
        m = re.search(r'"action"\s*:\s*"(\w+)"', a)
        c[m.group(1) if m else "?"] += 1
    return c


def main():
    v2 = DATA / "v2"
    train = load(v2 / "train.jsonl")
    val = load(v2 / "val.jsonl")
    test = load(v2 / "test.jsonl")
    base_n = len(train)

    starter = dedup(load(DEMOS / "starter.jsonl")) if (DEMOS / "starter.jsonl").exists() else []
    dialog = dedup(load(DEMOS / "dialog.jsonl")) if (DEMOS / "dialog.jsonl").exists() else []

    train = train + starter * 3 + dialog * 4
    rng.shuffle(train)

    out = DATA / "v4"
    out.mkdir(parents=True, exist_ok=True)
    for name, xs in [("train", train), ("val", val), ("test", test)]:
        with open(out / f"{name}.jsonl", "w") as f:
            for x in xs:
                f.write(json.dumps(x) + "\n")

    mix = action_mix(train)
    tot = sum(mix.values())
    heal_pct = 100 * mix.get("heal_at_center", 0) / tot
    stats = {
        "version": "v4",
        "base": "v2 train (proven navigation) + starter x3 + dialog x4",
        "train": len(train), "val": len(val), "test": len(test),
        "v2_base_examples": base_n, "starter_unique": len(starter), "dialog_unique": len(dialog),
        "heal_pct": round(heal_pct, 2),
        "action_mix": dict(mix.most_common()),
    }
    (out / "stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1))
    print(f"\nheal_at_center = {heal_pct:.1f}% (v2 was ~1%, v3 was ~7% and collapsed)")
    assert heal_pct < 3, "heal fraction too high -- would risk the v3 collapse"
    print("OK: heal balance preserved; v4 dataset written ->", out)


if __name__ == "__main__":
    main()
