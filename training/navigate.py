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
from functools import lru_cache
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "gamedata" / "maps"

TILESET_BST = {
    "OVERWORLD": "overworld", "FOREST": "forest", "CAVERN": "cavern",
    "REDS_HOUSE_1": "reds_house", "REDS_HOUSE_2": "reds_house", "DOJO": "gym",
    "POKECENTER": "pokecenter", "GYM": "gym", "HOUSE": "house",
    "FOREST_GATE": "gate", "MUSEUM": "gate", "UNDERGROUND": "underground",
    "GATE": "gate", "SHIP": "ship", "SHIP_PORT": "ship_port", "CEMETERY": "cemetery",
    "INTERIOR": "interior", "CAVE": "cavern", "LOBBY": "lobby", "MANSION": "mansion",
    "LAB": "lab", "CLUB": "club", "FACILITY": "facility", "PLATEAU": "plateau",
}
COLL_NAME = {
    "overworld": "Overworld_Coll", "forest": "Forest_Coll", "cavern": "Cavern_Coll",
    "pokecenter": "Lobby_Coll", "gym": "Club_Coll", "house": "Facility_Coll",
    "gate": "Lobby_Coll", "ship": "Facility_Coll", "ship_port": "Facility_Coll",
    "cemetery": "Facility_Coll", "interior": "Facility_Coll", "lobby": "Lobby_Coll",
    "mansion": "Mansion_Coll", "lab": "Lab_Coll", "club": "Club_Coll",
    "facility": "Facility_Coll", "plateau": "Plateau_Coll", "underground": "Facility_Coll",
    "reds_house": "Facility_Coll",
}


@lru_cache(maxsize=1)
def registry():
    """map_id -> {name, wb, hb, blk, bst, coll:set}"""
    const = (ASSETS / "meta" / "map_constants.asm").read_text()
    colls = {}
    ct = (ASSETS / "meta" / "collision_tile_ids.asm").read_text()
    for m in re.finditer(r'(\w+_Coll)::\s*\n\s*coll_tiles\s+([^\n]*)', ct):
        colls[m.group(1)] = {int(x, 16) for x in re.findall(r'\$([0-9a-f]+)', m.group(2))}
    reg = {}
    mid = 0
    for m in re.finditer(r'map_const\s+(\w+),\s*(\d+),\s*(\d+)', const):
        name_c, wb, hb = m.group(1), int(m.group(2)), int(m.group(3))
        # constant -> CamelCase file name: match against blk files
        camel = "".join(p.title() for p in name_c.split("_"))
        # fix common numeric/route patterns (ROUTE_1 -> Route1, MT_MOON_1F -> MtMoon1F ...)
        camel = re.sub(r'(\d)F$', lambda g: g.group(1) + "F", camel)
        entry = {"name": camel, "wb": wb, "hb": hb, "id": mid}
        blk = ASSETS / "blk" / f"{camel}.blk"
        hdr = ASSETS / "headers" / f"{camel}.asm"
        if blk.exists() and hdr.exists():
            ts = re.search(r'map_header\s+\w+,\s*\w+,\s*(\w+)', hdr.read_text())
            bst = TILESET_BST.get(ts.group(1), "overworld") if ts else "overworld"
            entry.update(blk=blk, bst=bst,
                         coll=colls.get(COLL_NAME.get(bst, "Overworld_Coll"), colls["Overworld_Coll"]))
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
