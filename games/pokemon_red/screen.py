"""Game-general screen perception for Pokemon Red.

Every Game Boy game renders the visible 20x18 background to a tilemap; pokered keeps
a live copy in WRAM at wTileMap (0xC3A0). We decode that copy to text via the game's
font charmap, and read the menu cursor index (wCurrentMenuItem 0xCC26). This is what a
human sees on screen -- no blind macros, real perception the model can act on.

Per-game cost = one CHARMAP table (data), not control code.
"""

WTILEMAP = "C3A0"          # wTileMap: 20x18 visible bg tilemap copy in WRAM
TILEMAP_LEN = 20 * 18      # 360 bytes, row-major
WCURRENTMENUITEM = "CC26"  # index of the highlighted menu row (0-based)
WTEXTBOXID = "D125"        # nonzero-ish when a text box / menu frame is drawn

# pokered font charmap: tile id -> character (see pokered/charmap.asm, English).
CHARMAP = {0x50: "", 0x7F: " ", 0x4E: "\n", 0x4F: " "}
for i in range(26):
    CHARMAP[0x80 + i] = chr(ord("A") + i)   # A-Z  = 0x80..0x99
    CHARMAP[0xA0 + i] = chr(ord("a") + i)   # a-z  = 0xA0..0xB9
for i, ch in enumerate("():;[]"):
    CHARMAP[0x9A + i] = ch                   # 0x9A..0x9F
for i in range(10):
    CHARMAP[0xF6 + i] = str(i)               # 0-9  = 0xF6..0xFF
CHARMAP.update({
    0xBA: "e", 0xE0: "'", 0xE1: "PK", 0xE2: "MN", 0xE3: "-", 0xE4: "'r",
    0xE5: "'m", 0xE6: "?", 0xE7: "!", 0xE8: ".", 0xEF: "♂", 0xF1: "x",
    0xF3: "/", 0xF4: ",", 0xF5: "♀", 0xEC: "▷", 0xED: "▶",
    0xBB: "'d", 0xBC: "'l", 0xBD: "'s", 0xBE: "'t", 0xBF: "'v",
})


def decode_tiles(raw: bytes) -> list[str]:
    """20x18 tile bytes -> 18 rows of decoded text."""
    rows = []
    for r in range(18):
        chunk = raw[r * 20:(r + 1) * 20]
        rows.append("".join(CHARMAP.get(b, " ") for b in chunk))
    return rows


def read_screen(emu) -> dict:
    """Return {'rows': [...], 'text': str, 'menu_index': int, 'has_box': bool}.

    `emu` is an executor.Emu (has .peekblock). Read-only; no state mutation.
    """
    raw = emu.peekblock(WTILEMAP, TILEMAP_LEN)
    rows = decode_tiles(raw)
    # collapse to readable lines (strip trailing spaces, drop blank lines)
    lines = [ln.rstrip() for ln in rows]
    text = "\n".join(ln for ln in lines if ln.strip())
    cc = emu.peekblock(WCURRENTMENUITEM, 1)
    menu_index = cc[0] if cc else 0
    tb = emu.peekblock(WTEXTBOXID, 1)
    has_box = bool(tb and tb[0])
    return {"rows": rows, "text": text, "menu_index": menu_index, "has_box": has_box}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __import__("os").path.dirname(__file__))
    import executor as X
    st = sys.argv[1] if len(sys.argv) > 1 else "run/ck-lab-before-starter.state"
    import shutil, os
    tmp = "run/_screentest.state"
    shutil.copy(st, tmp)
    e = X.Emu(tmp)
    scr = read_screen(e)
    print("=== decoded screen (20x18) ===")
    for r in scr["rows"]:
        print("|" + r + "|")
    print("=== text ===")
    print(scr["text"])
    print(f"=== menu_index={scr['menu_index']} has_box={scr['has_box']} ===")
    os.remove(tmp)
