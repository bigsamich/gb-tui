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
            return {"visited": set(d.get("visited", [])), "warps": d.get("warps", {})}
        except Exception:
            pass
    return {"visited": set(), "warps": {}}


def save(tag, mem):
    _f(tag).write_text(json.dumps({"visited": sorted(mem["visited"]), "warps": mem["warps"]}))


def observe(mem, last, cur) -> bool:
    """Record cur map as visited; if the map just changed and we were standing on a warp
    tile of the previous map, record that warp's discovered destination. Returns True if
    memory changed (so the caller can persist)."""
    changed = cur[0] not in mem["visited"]
    mem["visited"].add(cur[0])
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
