"""Serving shim: state JSON in -> action JSON out, via Ollama.

Guarantees train/inference parity by reusing context.py + prompts.py.

Usage (one decision per call):
  echo '{"state_text":"...","goal":"...","ctx":{...}}' | python3 serve_shim.py --model pokered-8b
`ctx` uses the context.build_facts() schema (in_battle, enemy_species, our_species,
our_moves, map_name, party). Prints the action JSON on stdout (or {"error":...}).
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context as C
import prompts


def ask(model, messages, url, think=True):
    body = json.dumps({
        "model": model, "messages": messages, "stream": False,
        "think": think,
        "options": {"temperature": 0.1, "num_predict": 400},
    }).encode()
    req = urllib.request.Request(f"{url}/api/chat", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())["message"]["content"]


def extract_action(text):
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
    ap.add_argument("--model", default="pokered-8b")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--no-think", action="store_true")
    args = ap.parse_args()

    req = json.loads(sys.stdin.read())
    facts = C.build_facts(req.get("ctx", {}))
    user = ""
    if facts:
        user += f"[FACTS]\n{facts}\n\n"
    user += f"[STATE]\n{req['state_text']}\n\n[GOAL] {req.get('goal','Continue the playthrough.')}"
    messages = [{"role": "system", "content": prompts.SYSTEM},
                {"role": "user", "content": user}]
    out = ask(args.model, messages, args.url, think=not args.no_think)
    act = extract_action(out)
    print(json.dumps(act if act else {"error": "no_action", "raw": out[-400:]}))


if __name__ == "__main__":
    main()
