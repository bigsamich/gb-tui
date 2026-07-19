# Bobby's Pokémon Red run — AI play protocol

## Mission
New game as **Bobby** (done), starter **Charmander** (done), beat **Brock** for the
Boulder Badge (in progress).

## Harness

```
./target/release/gb-agent --rom test-roms/pokemon-red.gb --state run/bobby.state \
  --script "<tokens>" --shot run/shot.png --peek 2>/dev/null | grep -v WARNING
```

- Script tokens: `up/down/left/right/a/b/start/select:<frames>`, `wait:N`, `mash-a:N`.
- One walking step = 16 frames of a held direction. Direction changes eat ~2 frames to turn.
- `--peek` prints: map, x, y, badges, party count, mon1 level/hp, in_battle
  (0 none, 1 wild, 2 trainer). `--peekhex ADDR:LEN` dumps RAM.
- Read `run/shot.png` (3x-scaled PNG) after every action; verify with peeks.
- Checkpoint before anything irreversible: `cp run/bobby.state run/ck-<name>.state`.

## Hard-won lessons

1. **Never blind-mash A near menus.** Mashing selected NEW NAME and typed "AAAAAAA"
   once (fixed by restart). Approach every menu with single `a:2` + screenshot.
2. **Dialogue loop trap:** after talking to an NPC, extra A presses re-open their text.
   Close with **B**, then move away.
3. **Battle ITEM trap:** mash in battle can drift onto ITEM and get stuck in the bag
   ("This isn't yours to use!" loops). Escape: `a:2` (dismiss error) `b:2` (close bag)
   `up:2` (cursor ITEM→FIGHT) then attack.
4. **Safe battle round macro** (cursor self-corrects to FIGHT):
   `b:2 wait:8 a:2 wait:15 a:2 wait:140` — repeat ~5x per call, peek in_battle after.
5. **Ledges are one-way (south).** Walking down over a ledge hops it — good southbound,
   blocks northbound. Viridian's south ledge gap is at column **x=15**; cross there.
6. **Text prints slowly** (~6 frames/char). A presses during printing are wasted;
   mash-a advances ~1 box per 4-6 cycles.
7. Name entry grid is 9 cols; SELECT toggles lower case; Start jumps to END.

## Map/RAM crib

- Maps seen: 0 Pallet, 12 Route 1, 1 Viridian, 37/38 Bobby's house, 39 Gary's?, 40 Oak lab,
  41 Viridian PC (heal: up to counter, A, A on HEAL, wait 300), 42 Viridian Mart.
- Viridian PC door ≈ (23,25) reachable from below at (23,26); Mart door ≈ (29,19) from (29,20).
- Player name D158 (Bobby = 81 ae a1 a1 b8 50). Rival GARY. Bag: D31D count, D31E pairs.
  Badges D356 (bit0 = Boulder). Party count D163, species D164 (Charmander=0xB0).
- Pokédex: acquired (D2F7 bit set for Charmander).

## Progress log

- ck-named-bobby → ck-got-charmander → ck-rival1-won (beat Gary's Squirtle, L6)
- Route 1 north (grass at x=12+ is the path around tree rows; ledge gaps found by probing)
- ck-viridian-healed (PC), ck-parcel (Mart clerk), returned south, ck-pokedex (delivered)
- Charmander L7, 17/23 HP. Money untouched (~3175₽? unverified).

## Next steps

1. Return north to Viridian (Route 1, grass path). Heal at PC.
2. Mart: buy 4-6 Poké Balls + 2-3 Potions (talk clerk, BUY menu — careful cursor work).
3. Optional but recommended: Route 22 (west of Viridian), catch **Nidoran♂**
   (learns Double Kick at L12 — shreds Brock). Weaken with 1-2 Scratches, throw ball.
4. North through Viridian to Route 2 → **Viridian Forest**: grind Charmander to ~L12-13
   (learns Ember at L9); fight the Bug Catchers for XP.
5. Pewter City: heal, then **Brock**: Geodude L12, Onix L14. Lead with Nidoran Double Kick
   (if caught) or overleveled Charmander (Ember is resisted — expect a slog; stock Potions).
6. Verify badge: peek D356 bit0 = 1 → Boulder Badge. Checkpoint ck-boulder-badge.

## MISSION COMPLETE — 2026-07-19

- Bobby (player), rival GARY, starter Charmander -> CHARMELEON L18 (Scratch/Growl/Ember/Leer)
- PIKACHU L5 caught in Viridian Forest via ExtraTricky DSum walk (2nd pattern: L4 Weedle seed
  -> 15-out -> L5 Kakuna -> 19-out -> PIKACHU). 3 balls used. Only Pikachu caught
  (an accidental Rattata was erased via state rollback).
- Whiteouts: 1 (Route 2 poison, pre-Antidotes).
- BROCK DEFEATED: Growl-stacked Geodude to Tackle=1, Ember x5 (3-10 dmg); Onix fell to
  3 Embers (17+16+crit) — Onix's Special is 30, Ember shreds despite rock resist.
- badges=0x01 (Boulder Badge), money 1713, Pewter PC is respawn.
- Key battle lesson: move cursor resets/drifts; ALWAYS verify move menu by screenshot
  before firing. L17 Leer changed the moveset to 4 slots (breaks down-clamp macros).
- States: ck-BOULDER-BADGE.state (post-badge), ck-BEFORE-BROCK.state (retry point).
