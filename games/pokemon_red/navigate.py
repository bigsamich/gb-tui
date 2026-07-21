"""Map registry + BFS navigation over the pokered assets, for ALL maps.

Registry is auto-built from:
  assets/gamedata/maps/meta/map_constants.asm   (id -> name, width, height in blocks)
  assets/gamedata/maps/headers/<Name>.asm       (tileset)
  assets/gamedata/maps/meta/collision_tile_ids.asm (passable tiles per tileset)
  assets/gamedata/maps/blk + blocksets/*.bst

Collision rule (verified in-game on Mt Moon): a player cell (x,y) is walkable iff
bottom-left subtile is in the tileset's passable list AND top-left subtile is not
the cavern cliff tile 0x29.
"""

import re
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / ".git").exists()) / "pipeline"))
import _bootstrap  # noqa: E402

ASSETS = _bootstrap.KNOWLEDGE / "gamedata" / "maps"

# Authoritative per-tileset (blockset file, collision group), from the
# disassembly's gfx/tilesets.asm INCBIN aliases + collision_tile_ids.asm groups.
TILESET_INFO = {
    "OVERWORLD": ("overworld", "Overworld_Coll"),
    "REDS_HOUSE_1": ("reds_house", "RedsHouse1_Coll"),
    "MART": ("pokecenter", "Mart_Coll"),
    "FOREST": ("forest", "Forest_Coll"),
    "REDS_HOUSE_2": ("reds_house", "RedsHouse2_Coll"),
    "DOJO": ("gym", "Dojo_Coll"),
    "POKECENTER": ("pokecenter", "Pokecenter_Coll"),
    "GYM": ("gym", "Gym_Coll"),
    "HOUSE": ("house", "House_Coll"),
    "FOREST_GATE": ("gate", "Gate_Coll"),
    "MUSEUM": ("gate", "Museum_Coll"),
    "UNDERGROUND": ("underground", "Underground_Coll"),
    "GATE": ("gate", "Gate_Coll"),
    "SHIP": ("ship", "Ship_Coll"),
    "SHIP_PORT": ("ship_port", "ShipPort_Coll"),
    "CEMETERY": ("cemetery", "Cemetery_Coll"),
    "INTERIOR": ("interior", "Interior_Coll"),
    "CAVERN": ("cavern", "Cavern_Coll"),
    "LOBBY": ("lobby", "Lobby_Coll"),
    "MANSION": ("mansion", "Mansion_Coll"),
    "LAB": ("lab", "Lab_Coll"),
    "CLUB": ("club", "Club_Coll"),
    "FACILITY": ("facility", "Facility_Coll"),
    "PLATEAU": ("plateau", "Plateau_Coll"),
}


@lru_cache(maxsize=1)
def registry():
    """map_id -> {name, wb, hb, blk, bst, coll:set}"""
    const = (ASSETS / "meta" / "map_constants.asm").read_text()
    colls = {}
    ct = (ASSETS / "meta" / "collision_tile_ids.asm").read_text()
    pending = []           # chained labels alias the next coll_tiles list
    for line in ct.splitlines():
        lm = re.match(r'\s*(\w+_Coll)::', line)
        if lm:
            pending.append(lm.group(1))
            continue
        tm = re.match(r'\s*coll_tiles\s+(.*)', line)
        if tm and pending:
            ids = {int(x, 16) for x in re.findall(r'\$([0-9a-f]+)', tm.group(1))}
            for name in pending:
                colls[name] = ids
            pending = []
    reg = {}
    mid = 0
    for m in re.finditer(r'map_const\s+(\w+),\s*(\d+),\s*(\d+)', const):
        name_c, wb, hb = m.group(1), int(m.group(2)), int(m.group(3))
        # constant -> CamelCase file name: match against blk files
        camel = "".join(p.title() for p in name_c.split("_"))
        # fix common numeric/route patterns (ROUTE_1 -> Route1, MT_MOON_1F -> MtMoon1F ...)
        camel = re.sub(r'(\d)F$', lambda g: g.group(1) + "F", camel)
        entry = {"name": camel, "const": name_c, "wb": wb, "hb": hb, "id": mid}
        blk = ASSETS / "blk" / f"{camel}.blk"
        hdr = ASSETS / "headers" / f"{camel}.asm"
        if blk.exists() and hdr.exists():
            ts = re.search(r'map_header\s+\w+,\s*\w+,\s*(\w+)', hdr.read_text())
            bst, collname = TILESET_INFO.get(ts.group(1) if ts else "OVERWORLD",
                                             ("overworld", "Overworld_Coll"))
            entry.update(blk=blk, bst=bst,
                         coll=colls.get(collname, colls["Overworld_Coll"]))
        reg[mid] = entry
        mid += 1
    return reg


@lru_cache(maxsize=64)
def grid(map_id: int):
    """-> (walkable[[bool]], w, h) or None"""
    e = registry().get(map_id)
    if not e or "blk" not in e:
        return None
    b = e["blk"].read_bytes()
    t = (ASSETS / "blocksets" / f"{e['bst']}.bst").read_bytes()
    wb, hb = e["wb"], e["hb"]
    T = [[0] * (wb * 4) for _ in range(hb * 4)]
    for by in range(hb):
        for bx in range(wb):
            if by * wb + bx >= len(b):
                break
            bb = t[b[by * wb + bx] * 16: b[by * wb + bx] * 16 + 16]
            for i, tt in enumerate(bb):
                T[by * 4 + i // 4][bx * 4 + i % 4] = tt
    w, h = wb * 2, hb * 2
    coll = e["coll"]
    g = [[(T[2 * y + 1][2 * x] in coll) and (T[2 * y][2 * x] != 0x29)
          for x in range(w)] for y in range(h)]
    return g, w, h


def bfs_path(map_id: int, start, goal, blocked=frozenset()):
    """-> list of 'u/d/l/r' steps or None"""
    gg = grid(map_id)
    if not gg:
        return None
    g, w, h = gg
    from collections import deque
    q = deque([tuple(start)])
    prev = {tuple(start): None}
    goal = tuple(goal)
    while q:
        cur = q.popleft()
        if cur == goal:
            break
        x, y = cur
        for dx, dy, d in ((0, -1, 'u'), (0, 1, 'd'), (-1, 0, 'l'), (1, 0, 'r')):
            n = (x + dx, y + dy)
            if 0 <= n[0] < w and 0 <= n[1] < h and n not in prev \
               and g[n[1]][n[0]] and n not in blocked:
                prev[n] = (cur, d)
                q.append(n)
    if goal not in prev:
        return None
    path, node = [], goal
    while prev.get(node) is not None:
        node, d = prev[node]
        path.append(d)
    return list(reversed(path))


@lru_cache(maxsize=1)
def _const_to_id():
    return {e["const"]: mid for mid, e in registry().items()}


@lru_cache(maxsize=256)
def connections(map_id: int):
    """map_id -> {'north'|'south'|'east'|'west': connected_map_id} from the header."""
    e = registry().get(map_id)
    if not e:
        return {}
    hdr = ASSETS / "headers" / f"{e['name']}.asm"
    if not hdr.exists():
        return {}
    c2i = _const_to_id()
    out = {}
    for m in re.finditer(r'connection\s+(\w+),\s*\w+,\s*(\w+),', hdr.read_text()):
        d, const = m.group(1), m.group(2)
        if const in c2i:
            out[d] = c2i[const]
    return out


def _nearest_reachable_on_edge(map_id, start, direction):
    """BFS-reachable tile closest to the given edge; returns (path_steps, edge_tile)."""
    gg = grid(map_id)
    if not gg:
        return None, None
    g, w, h = gg
    from collections import deque
    q = deque([tuple(start)])
    prev = {tuple(start): None}
    best = None
    while q:
        cur = q.popleft()
        x, y = cur
        # score how close this cell is to the target edge (0 = on the edge)
        edge = {"north": y, "south": h - 1 - y, "west": x, "east": w - 1 - x}[direction]
        if best is None or edge < best[0]:
            best = (edge, cur)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            n = (x + dx, y + dy)
            if 0 <= n[0] < w and 0 <= n[1] < h and n not in prev and g[n[1]][n[0]]:
                prev[n] = cur
                q.append(n)
    if not best:
        return None, None
    # reconstruct path start->best cell
    node = best[1]
    path = []
    while prev.get(node) is not None:
        p = prev[node]
        dx, dy = node[0] - p[0], node[1] - p[1]
        path.append({(0, -1): 'u', (0, 1): 'd', (-1, 0): 'l', (1, 0): 'r'}[(dx, dy)])
        node = p
    return list(reversed(path)), best[1]


def cross_edge_script(map_id, start, direction):
    """Full button script: walk to the map's <direction> edge and step OFF it to
    trigger the overworld connection. None if that edge has no connection."""
    if direction not in connections(map_id):
        return None
    path, _tile = _nearest_reachable_on_edge(map_id, start, direction)
    if path is None:
        return None
    press = {"north": "up", "south": "down", "west": "left", "east": "right"}[direction]
    parts = []
    # walk the path in straight runs
    i = 0
    while i < len(path):
        d0 = path[i]
        k = 0
        while i < len(path) and path[i] == d0:
            k += 1; i += 1
        parts.append(f"{ {'u':'up','d':'down','l':'left','r':'right'}[d0] }:{k*16} wait:10")
    # step off the edge (extra presses to cross the transition)
    parts.append(f"{press}:32 wait:40 {press}:16 wait:30")
    return " ".join(parts)


def steps_to_script(steps, cap=6):
    """First straight segment (<=cap steps) -> gb-agent button script."""
    if not steps:
        return None
    d0 = steps[0]
    k = 0
    while k < len(steps) and steps[k] == d0 and k < cap:
        k += 1
    name = {'u': 'up', 'd': 'down', 'l': 'left', 'r': 'right'}[d0]
    return f"{name}:{k * 16} wait:10"


if __name__ == "__main__":
    r = registry()
    print("maps in registry:", sum(1 for e in r.values() if "blk" in e), "/", len(r))
    for mid, nm in [(3, "CeruleanCity"), (59, "MtMoon1F"), (15, "Route4")]:
        e = r[mid]
        assert e["name"] == nm, (mid, e["name"])
        print(mid, e["name"], e["wb"], "x", e["hb"], e.get("bst"))
    p = bfs_path(59, (14, 35), (5, 5))
    print("MtMoon1F (14,35)->(5,5):", len(p) if p else None, "steps")
    p2 = bfs_path(3, (0, 18), (19, 18))
    print("Cerulean (0,18)->(19,18):", len(p2) if p2 else None, "steps")
