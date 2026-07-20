"""Autonomous 'beat Brock' quest fleet — NO route guidance.

Workers start from the same early checkpoint (Oak's lab, Charmander L6, 0 badges)
and get only generic competencies:
  - novelty-seeking exploration: BFS toward the nearest never-visited tile on the
    current map (warps/doors get stepped on naturally and change maps)
  - basic battle survival: strongest move by type math; flee wild fights when weak
  - dialog/NPC unstick (B-mash), and blackouts are tolerated (the game's own retry)
Success = Boulder Badge bit set. Every decision + periodic progress is logged.

Usage: python3 brock_quest.py --workers 3 --ticks 3000
"""

import argparse
import json
import multiprocessing as mp
import os
import random
import shutil
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context as C
import executor as X
import navigate as NAV

ROOT = Path(__file__).resolve().parent.parent
ROLL = Path(__file__).resolve().parent / "rollouts"
GOAL = ("Beat Misty, the Cerulean City Gym Leader, for the Cascade Badge. "
        "First beat Brock in Pewter, then cross Mt Moon to reach Cerulean.")


def frontier_step(map_id, pos, visited, blocked):
    """BFS to the nearest tile not yet visited; return first-step direction char."""
    gg = NAV.grid(map_id)
    if not gg:
        return None
    g, w, h = gg
    q = deque([tuple(pos)])
    prev = {tuple(pos): None}
    target = None
    while q:
        cur = q.popleft()
        if cur != tuple(pos) and (map_id, cur) not in visited:
            target = cur
            break
        x, y = cur
        for dx, dy, d in ((0, -1, 'u'), (0, 1, 'd'), (-1, 0, 'l'), (1, 0, 'r')):
            n = (x + dx, y + dy)
            if 0 <= n[0] < w and 0 <= n[1] < h and n not in prev \
               and g[n[1]][n[0]] and (map_id, n) not in blocked:
                prev[n] = (cur, d)
                q.append(n)
    if target is None:
        return None
    node, first = target, None
    while prev.get(node) is not None:
        node, d = prev[node]
        first = d
    return first


_WARPS = {}


def warps_of(map_id):
    """All warp tiles (incl. LAST_MAP exits) for a map, from its objects file."""
    if map_id in _WARPS:
        return _WARPS[map_id]
    import re
    e = NAV.registry().get(map_id, {})
    tiles = set()
    f = ROOT / "assets/gamedata/maps/objects" / f"{e.get('name','?')}.asm"
    if f.exists():
        for m in re.finditer(r'warp_event\s+(\d+),\s*(\d+),', f.read_text()):
            tiles.add((int(m.group(1)), int(m.group(2))))
    _WARPS[map_id] = tiles
    return tiles


def off_edge_dir(map_id, pos):
    """Direction to press when standing ON a warp tile: walk off the map edge.
    (Gen-1 door mats only warp when you walk off them toward the edge.)"""
    gg = NAV.grid(map_id)
    if not gg:
        return 'd'
    _, w, h = gg
    x, y = pos
    if y >= h - 2:
        return 'd'
    if y <= 1:
        return 'u'
    if x <= 1:
        return 'l'
    if x >= w - 2:
        return 'r'
    return 'd'


# Gen-1 wild slot chances (out of 256), in table order
SLOT_CHANCE = [51, 51, 39, 25, 25, 25, 13, 13, 11, 3]


def rarity(species, map_name):
    """Encounter probability of species on this map (0..1); 1.0 if unknown."""
    _, _, _, enc = C._db()
    e = enc.get(map_name, {}).get("grass")
    if not e:
        return 1.0
    p = sum(SLOT_CHANCE[i] for i, row in enumerate(e["mons"][:10])
            if row["species"] == species)
    return p / 256 if p else 1.0


def battle_action(s, rng=None, map_name=""):
    active = s["party"][s.get("active_idx", 0)] if s["party"] else None
    if not active:
        return {"action": "press", "buttons": "b:8 wait:80"}, "advance"
    enemy = s.get("enemy_species", "")
    wild = s["in_battle"] == 1
    # --- rarity-based catching: rare mons are ALWAYS caught; no duplicates ---
    have = {p["species"] for p in s["party"]}
    if wild and rng is not None and s.get("balls", 0) > 0 \
       and enemy not in have and len(s["party"]) < 6:
        r = rarity(enemy, map_name)
        want = r <= 0.06 or (r <= 0.12 and rng.random() < 0.5) or rng.random() < 0.15
        if want:
            return {"action": "throw_ball"}, f"catch(r={r:.2f})"
    low = active["hp"] < max(6, active["max_hp"] * 0.2)
    pp_left = sum(1 for m, p in zip(active["moves"], active["pp"])
                  if p > 0 and (C.move(m) or {}).get("power", 0) > 0)
    if wild and (low or pp_left == 0):
        return {"action": "flee"}, "weak-flee"
    best, ranked = C.best_move(active["moves"], active["species"], enemy)
    # --- matchup switching: if we're resisted and a teammate hits harder ---
    if best and ranked and len(s["party"]) > 1 and rng is not None:
        e = C.mon(enemy)
        act_mult = ranked[0][2] or 1.0
        if e and act_mult < 1.0:
            for p in s["party"]:
                if p["species"] == active["species"] or p["hp"] < p["max_hp"] * 0.3:
                    continue
                b2, r2 = C.best_move(p["moves"], p["species"], enemy)
                if b2 and r2 and (r2[0][2] or 1.0) > act_mult:
                    return {"action": "switch", "to": p["species"]}, f"switch-{p['species']}"
    if best and ranked and ranked[0][1] > 0:
        return {"action": "fight", "move": best}, f"fight-{best}"
    return {"action": "fight", "move": active["moves"][0] if active["moves"] else ""}, "fight-any"


def connections_of(map_id):
    """[(direction, edge-walk info)] from the map header."""
    import re
    e = NAV.registry().get(map_id, {})
    f = ROOT / "assets/gamedata/maps/headers" / f"{e.get('name','?')}.asm"
    out = []
    if f.exists():
        for m in re.finditer(r'connection\s+(\w+),\s*(\w+),', f.read_text()):
            out.append((m.group(1), m.group(2)))
    return out


def exit_candidates(map_id):
    """All ways out of a map: warp tiles and connection edges."""
    cands = [("warp", (x, y)) for (x, y) in warps_of(map_id)]
    gg = NAV.grid(map_id)
    if gg:
        g, w, h = gg
        for direction, dest in connections_of(map_id):
            if direction == "north":
                tiles = [(x, 0) for x in range(w) if g[0][x]]
            elif direction == "south":
                tiles = [(x, h - 1) for x in range(w) if g[h - 1][x]]
            elif direction == "west":
                tiles = [(0, y) for y in range(h) if g[y][0]]
            else:
                tiles = [(w - 1, y) for y in range(h) if g[y][w - 1]]
            if tiles:
                cands.append((f"conn-{direction}", tiles[len(tiles) // 2]))
    return cands


def worker(idx, src, ticks, scratch):
    os.nice(15)
    rng = random.Random(1000 + idx)
    state = Path(scratch) / f"q{idx}.state"
    shutil.copy(src, state)
    emu = X.Emu(state)
    emu.run(f"wait:{23 + idx * 71}")            # RNG divergence
    # clear any opening cutscene/dialog: B-mash until we can actually move
    for _ in range(40):
        b = emu.snapshot()
        if b["in_battle"]:
            break
        emu.run("b:8 wait:90 down:16 wait:14")
        a = emu.snapshot()
        if (a["x"], a["y"]) != (b["x"], b["y"]) or a["map"] != b["map"]:
            break
    log = open(ROLL / f"brock-w{idx}-{int(time.time())}.jsonl", "w")
    visited, blocked, exits_used = set(), set(), {}
    stuck, faints, last = 0, 0, None
    prev_fainted = False
    target_exit = None       # (kind, tile) currently navigating to
    last_dir = None          # direction of the previous movement attempt
    DIRS = {'u': "up:16 wait:8", 'd': "down:16 wait:8",
            'l': "left:16 wait:8", 'r': "right:16 wait:8"}
    CONN_DIR = {"conn-north": 'u', "conn-south": 'd', "conn-west": 'l', "conn-east": 'r'}
    brock_done = False
    for t in range(ticks):
        s = emu.snapshot()
        if (s["badges"] & 1) and not brock_done:
            brock_done = True
            log.write(json.dumps({"event": "BROCK-BEATEN", "tick": t, "faints": faints}) + "\n")
            print(f"[w{idx}] ** BROCK-BEATEN at tick {t} (faints={faints}) **", flush=True)
        if s["badges"] & 2:      # Cascade Badge = Misty beaten
            log.write(json.dumps({"event": "MISTY-BEATEN", "tick": t, "faints": faints}) + "\n")
            print(f"[w{idx}] *** MISTY-BEATEN at tick {t} (faints={faints}) ***", flush=True)
            break
        if s["in_battle"]:
            mapname = NAV.registry().get(s["map"], {}).get("name", "")
            act, why = battle_action(s, rng=rng, map_name=mapname)
            st, ctx = X.state_text(s), X.ctx_for(s)
            log.write(json.dumps({"scenario": f"brock-w{idx}", "step": t, "facts": C.build_facts(ctx),
                                  "state_text": st, "ctx": ctx, "goal": GOAL,
                                  "action": act, "why": why, "policy": "generic",
                                  "snap": {k: v for k, v in s.items() if k != "party"}}) + "\n")
            emu.do(act, s)
            continue
        if any(p["hp"] == 0 for p in s["party"]):
            prev_fainted = True
        elif prev_fainted and all(p["hp"] == p["max_hp"] for p in s["party"]):
            faints += 1
            prev_fainted = False
        pos = (s["x"], s["y"])
        key = (s["map"], pos)
        if last is not None and last == key:
            stuck += 1
            # learn the obstacle: the tile we last tried to enter is blocked (NPC etc.)
            if last_dir is not None:
                dx, dy = {'u': (0, -1), 'd': (0, 1), 'l': (-1, 0), 'r': (1, 0)}[last_dir]
                blocked.add((s["map"], (pos[0] + dx, pos[1] + dy)))
            # dialogs close with B (never A while exploring — A talks to NPCs like
            # Oak and opens a dialog loop). Then step in a genuinely OPEN direction.
            emu.run("b:8 wait:90")
            gg = NAV.grid(s["map"])
            if gg:
                g, w, h = gg
                opens = [d for d, (dx, dy) in
                         (('u', (0, -1)), ('d', (0, 1)), ('l', (-1, 0)), ('r', (1, 0)))
                         if 0 <= pos[0] + dx < w and 0 <= pos[1] + dy < h
                         and g[pos[1] + dy][pos[0] + dx]
                         and (s["map"], (pos[0] + dx, pos[1] + dy)) not in blocked]
                if opens:
                    emu.run(DIRS[rng.choice(opens)])
            if stuck >= 3:
                target_exit = None                     # re-choose exit
                stuck = 0
        else:
            stuck = 0
        last_dir = None
        if last is not None and last[0] != s["map"]:
            target_exit = None                          # arrived in a new map
        last = key
        visited.add(key)
        # doors are walls unless deliberately chosen: never path THROUGH a warp
        door_block = {(s["map"], wtile) for wtile in warps_of(s["map"])}
        # 1) frontier exploration of the current map
        if target_exit is None:
            d = frontier_step(s["map"], pos, visited, blocked | door_block)
            if d is not None:
                last_dir = d
                emu.run(DIRS[d])
                continue
            # 2) map exhausted -> choose the least-used exit
            cands = exit_candidates(s["map"])
            if not cands:
                emu.run(DIRS[rng.choice("udlr")])
                continue
            cands.sort(key=lambda c: (exits_used.get((s["map"], c[1]), 0), rng.random()))
            target_exit = cands[0]
            exits_used[(s["map"], target_exit[1])] = \
                exits_used.get((s["map"], target_exit[1]), 0) + 1
        # 3) navigate to and cross the chosen exit
        kind, tile = target_exit
        if pos == tile:
            d = CONN_DIR.get(kind, off_edge_dir(s["map"], pos))
            last_dir = d
            emu.run(DIRS[d])
        else:
            mb = {t2 for (m, t2) in blocked if m == s["map"]}
            mb |= {w for w in warps_of(s["map"]) if w != tile}   # other doors stay walls
            steps = NAV.bfs_path(s["map"], pos, tile, frozenset(mb))
            if steps:
                last_dir = steps[0]
                emu.run(NAV.steps_to_script(steps))
            else:
                target_exit = None
        if t % 50 == 0:
            lead = s["party"][0] if s["party"] else {}
            print(f"[w{idx}] t{t} map={s['map']} pos={pos} lead=L{lead.get('level')} "
                  f"hp={lead.get('hp')} maps_seen={len({m for m, _ in visited})} "
                  f"faints={faints}", flush=True)
    log.write(json.dumps({"event": "end", "ticks": t, "faints": faints,
                          "maps_seen": len({m for m, _ in visited})}) + "\n")
    log.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=None,
                    help="start state; default = random per worker among run/start-*.state")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--ticks", type=int, default=3000)
    args = ap.parse_args()
    ROLL.mkdir(exist_ok=True)
    scratch = Path(os.environ.get("TMPDIR", "/tmp")) / f"brock-{int(time.time())}"
    scratch.mkdir(parents=True)
    starters = sorted(ROOT.glob("run/start-*.state"))
    starters = [s for s in starters if "debug" not in s.name] or [ROOT / "run/ck-rival1-won.state"]
    rng0 = random.Random(int(time.time()))
    rng0.shuffle(starters)
    picks = [Path(args.state) if args.state else starters[i % len(starters)]
             for i in range(args.workers)]
    for i, p in enumerate(picks):
        print(f"worker {i} starter: {p.stem}", flush=True)
    procs = [mp.Process(target=worker, args=(i, str(picks[i]), args.ticks, str(scratch)))
             for i in range(args.workers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    print("quest fleet done")


if __name__ == "__main__":
    main()
