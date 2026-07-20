"""Offline eval: run a model (via Ollama or HF) on the held-out test set.

Metrics: valid-JSON rate, action-type match, exact-action match, and
battle-move optimality (does it pick the ground-truth best move?).

Usage:
  python3 eval_offline.py --data data/v1 --ollama pokered-8b
  python3 eval_offline.py --data data/v1 --ollama qwen3:8b        # stock baseline
"""

import argparse
import json
import re
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ask_ollama(model, messages, url="http://localhost:11434"):
    body = json.dumps({"model": model, "messages": messages, "stream": False,
                       "options": {"temperature": 0.1, "num_predict": int(__import__("os").environ.get("NUM_PREDICT", 400))}}).encode()
    req = urllib.request.Request(f"{url}/api/chat", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())["message"]["content"]


def extract_action(text: str):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    for m in reversed(list(re.finditer(r'\{[^{}]*\}', text))):
        try:
            j = json.loads(m.group(0))
            if "action" in j:
                return j
        except json.JSONDecodeError:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/v1")
    ap.add_argument("--ollama", required=True)
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    rows = [json.loads(l) for l in
            ((HERE / args.data) / "test.jsonl").read_text().splitlines()][:args.limit]
    n = len(rows)
    valid = act_match = exact = 0
    bm_total = bm_right = 0
    for i, r in enumerate(rows):
        gold = extract_action(r["messages"][2]["content"])
        out = ask_ollama(args.ollama, r["messages"][:2])
        pred = extract_action(out)
        if pred:
            valid += 1
            if gold and pred.get("action") == gold.get("action"):
                act_match += 1
                if pred == gold:
                    exact += 1
        if r.get("meta", {}).get("kind") == "battle_move" and gold:
            bm_total += 1
            if pred and pred.get("move") == gold.get("move"):
                bm_right += 1
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{n} valid={valid} act={act_match} exact={exact}")
    print(json.dumps({
        "model": args.ollama, "n": n,
        "valid_json": round(valid / n, 3),
        "action_type_match": round(act_match / n, 3),
        "exact_match": round(exact / n, 3),
        "battle_move_optimal": round(bm_right / bm_total, 3) if bm_total else None,
    }, indent=1))


if __name__ == "__main__":
    main()
