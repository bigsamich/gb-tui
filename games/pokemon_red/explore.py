"""Exploration + memory for interior/maze self-navigation (no hints).

The static map-graph router (context.route_*) handles the overworld, but can't route
through building/dungeon exits (LAST_MAP is dynamic) or mazes like Mt. Moon. There the
model must EXPLORE: try untried exits, remember where each led, and avoid re-treading.

This module gives the model that as PERCEPTION (not a decided action): a per-run memory
of visited maps + discovered warp destinations, rendered as an EXPLORE block listing the
current map's exits marked TRIED (-> where) or UNTRIED, and flagging any known exit that
leads toward the goal. The model then picks which exit to walk_to. Memory persists per run.
"""
import json

import _bootstrap  # noqa
import navigate as NAV
import context as C

RUN = _bootstrap.REPO_ROOT / "run"


def _f(tag):
    return RUN / f"{tag}.map.json"


def load(tag):
    p = _f(tag)
    if p.exists():
        try:
            d = json.loads(p.read_text())
            tiles = {int(k): {tuple(t) for t in v} for k, v in d.get("tiles", {}).items()}
            return {"visited": set(d.get("visited", [])), "warps": d.get("warps", {}),
                    "tiles": tiles}
        except Exception:
            pass
    return {"visited": set(), "warps": {}, "tiles": {}}


def save(tag, mem):
    _f(tag).write_text(json.dumps({
        "visited": sorted(mem["visited"]), "warps": mem["warps"],
        "tiles": {k: sorted(map(list, v)) for k, v in mem["tiles"].items()}}))


def observe(mem, last, cur) -> bool:
    """Record cur map+TILE as visited; if the map just changed and we were standing on a
    warp tile of the previous map, record that warp's discovered destination. Returns True
    if memory changed (so the caller can persist)."""
    changed = cur[0] not in mem["visited"]
    mem["visited"].add(cur[0])
    tset = mem["tiles"].setdefault(cur[0], set())
    if (cur[1], cur[2]) not in tset:                 # a newly-stepped tile
        tset.add((cur[1], cur[2]))
        changed = True
    if last and last[0] != cur[0]:
        lm, lx, ly = last
        nm = NAV.registry().get(lm, {}).get("name", "")
        for wx, wy, _dest in C.map_warps(nm):
            if (wx, wy) == (lx, ly):
                key = f"{lm}:{lx},{ly}"
                if mem["warps"].get(key) != cur[0]:
                    mem["warps"][key] = cur[0]
                    changed = True
                break
    return changed


def nearest_unexplored(mem, cur_map, sx, sy):
    """Flood-fill walkable tiles from (sx,sy); return the nearest tile we've NOT stepped on
    (the exploration frontier), or None if the reachable area is fully explored."""
    g_ = NAV.grid(cur_map)
    if not g_:
        return None
    g, w, h = g_
    visited = mem["tiles"].get(cur_map, set())
    from collections import deque
    q = deque([(sx, sy)])
    seen = {(sx, sy)}
    while q:
        x, y = q.popleft()
        if (x, y) != (sx, sy) and (x, y) not in visited:
            return (x, y)                      # first unstepped reachable tile
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            n = (x + dx, y + dy)
            if 0 <= n[0] < w and 0 <= n[1] < h and n not in seen and g[n[1]][n[0]]:
                seen.add(n)
                q.append(n)
    return None


def room_perception(mem, cur_map, x, y) -> str:
    """When the model is INSIDE its objective's area, don't route out -- explore it: point
    at the nearest ground it hasn't walked yet. Covering the room triggers trainers / the
    gym leader / reveals the puzzle. No location is hard-coded -- it's 'go where you have
    not been', derived from the run's own footprints."""
    frontier = nearest_unexplored(mem, cur_map, x, y)
    if frontier is None:
        return ("EXPLORE: you have walked most of this area. Interact with people/objects "
                "and try any exit to progress.")
    fx, fy = frontier
    d = []
    if fy < y:
        d.append("north")
    elif fy > y:
        d.append("south")
    if fx < x:
        d.append("west")
    elif fx > x:
        d.append("east")
    dirtxt = "/".join(d) if d else "here"
    return (f"EXPLORE: your objective is somewhere in THIS area -- you have not been to the "
            f"{dirtxt} part yet. walk_to ({fx},{fy}) to explore it; interact with anyone you "
            f"meet. Keep covering unexplored ground until you reach your objective.")


def perception(mem, cur_map, goal_map) -> str:
    """EXPLORE block: this map's exits, marked tried/untried, for the model to choose from."""
    nm = NAV.registry().get(cur_map, {}).get("name", "")
    warps = C.map_warps(nm)
    if len(warps) < 2:                     # nothing to choose between
        return ""
    reg = NAV.registry()
    fixed = {(x, y): did for x, y, did in C._warps_raw(nm)}   # statically-known dests
    lines, any_untried = [], False
    for wx, wy, disp in warps:
        key = f"{cur_map}:{wx},{wy}"
        dest_id = mem["warps"].get(key)
        if dest_id is None:
            dest_id = fixed.get((wx, wy))   # fall back to static dest if known
        if dest_id is None:
            lines.append(f"  ({wx},{wy}) -> ? UNTRIED")
            any_untried = True
        else:
            dn = reg.get(dest_id, {}).get("name", str(dest_id))
            toward = goal_map is not None and (dest_id == goal_map
                                               or C.route_hop(dest_id, goal_map) is not None)
            flag = " <== leads toward your objective" if toward else ""
            lines.append(f"  ({wx},{wy}) -> {dn}{flag}")
    hint = ("Pick an exit and walk_to it. Prefer one that leads toward your objective; "
            "otherwise try an UNTRIED exit to discover the way (don't re-take exits that "
            "loop you back).") if any_untried or True else ""
    return "EXPLORE (exits on this map):\n" + "\n".join(lines) + "\n" + hint
