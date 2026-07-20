# Pokémon Red — strategy guide (for Bobby's Charmander → Pikachu run)

Original guide written for this playthrough, grounded in the game data in `assets/` (gym rosters and levels are the exact values from the disassembly's trainer data; type multipliers from `json/typechart.json`). Team assumption: **Charmander line** (Fire) as the carry + **Pikachu** (Electric) as a special tool, catching only Pikachu per the run's rule.

Key team facts:
- **Charmander→Charmeleon→Charizard** (Charizard is Fire/**Flying**). Fire is 2× on Grass/Bug/Ice, 0.5× on Water/Rock/Fire/Dragon. Charizard's Flying half makes it **immune to Ground**.
- **Pikachu/Raichu** (Electric): 2× on Water/Flying, 0.5× on Grass/Electric/Dragon, **0× on Ground**. Its whole job is trivializing Water gyms.

---

## Gym progression (order, rosters, how our team handles it)

### 1. Brock — Pewter City · Rock ✅ (done)
`Geodude L12, Onix L14`. Rock resists Fire (0.5×). Won by out-leveling / scratch-and-Ember chip. Reward: TM34 Bide, Boulder Badge.

### 2. Misty — Cerulean City · Water  ← **current target**
`Staryu L18, Starmie L21`. **This is exactly why we're leveling Pikachu.** ThunderShock is **2× on both** (Water). A Pikachu around **L15–20 with ThunderShock one/two-shots both**. Charmeleon's Ember is 0.5× here — usable via level advantage but not ideal; lead with electric. Starmie is fast (Spd 115) and hits hard with Bubblebeam — bring Pikachu healthy. Reward: TM11 BubbleBeam, Cascade Badge, and HM01 Cut becomes usable.
- **Recommended team level: ~18–22.** Getting Pikachu to L20 (the current grind) makes this a clean sweep.

### 3. Lt. Surge — Vermilion City · Electric
`Voltorb L21, Pikachu L18, Raichu L24`. Pikachu is **useless here** (Electric 0.5× Electric). Charmeleon (Fire) is neutral — win by levels, or the classic answer is a **Ground type** (Dig from the Vermilion TM, or a Diglett/Sandshrew) since Ground is 2× and immune to their Electric. Raichu L24 is fast; watch for paralysis. *Gate:* the trash-can switch puzzle guards Surge. Reward: TM24 Thunderbolt (great on Pikachu), Thunder Badge.

### 4. Erika — Celadon City · Grass
`Victreebel L29, Tangela L24, Vileplume L29`. **Free win for Fire.** Charmeleon/Charizard Ember/Flamethrower is **2×** on all three. Watch Vileplume's Sleep Powder/status. Reward: TM21 Mega Drain, Rainbow Badge.

### 5. Koga — Fuchsia City · Poison
`Koffing L37, Muk L39, Koffing L37, Weezing L43`. Fire is neutral; **Psychic and Ground are 2×**. Charizard wins by levels/Flamethrower but expect Selfdestruct and status (Toxic, Sludge). A Psychic (Kadabra/Alakazam) walks it. Reward: TM06 Toxic, Soul Badge.

### 6. Sabrina — Saffron City · Psychic
`Kadabra L38, Mr. Mime L37, Venomoth L38, Alakazam L43`. **The hardest for our team** — nothing resists Psychic and Alakazam L43 has monster Special. Bug is 2× but we carry no strong Bug move. Answers: brute-force with a high-level Charizard, or bring a fast attacker to outspeed. Consider being **L45+** here. Reward: TM46 Psywave, Marsh Badge.

### 7. Blaine — Cinnabar Island · Fire
`Growlithe L42, Ponyta L40, Rapidash L42, Arcanine L47`. Fire vs Fire is 0.5× — **Charizard is a poor lead here.** Water/Rock/Ground are 2×. A Water type (or Surf) is the clean answer; otherwise out-level with non-Fire coverage. Reward: TM38 Fire Blast, Volcano Badge.

### 8. Giovanni — Viridian Gym · Ground
`Rhyhorn L45, Dugtrio L42, Nidoqueen L44, Nidoking L45, Rhydon L50`. **Charizard shines** — Fire/Flying is **immune to Ground** and to Dugtrio entirely. Watch the Nido line's mixed coverage (Ice Beam, Thunderbolt). Water/Grass/Ice are 2× on the Rock/Ground crew. Reward: TM27 Fissure, Earth Badge → Victory Road opens.

---

## Route path between gyms (overworld order)
Pallet → Route 1 → Viridian → Route 2 → **Viridian Forest** → Pewter (Brock) → Route 3 → **Mt. Moon** → Route 4 → **Cerulean (Misty)** → Nugget Bridge/Routes 24-25 (Bill) → Route 5 → Vermilion (**Surge**, needs Cut for the tree / S.S. Anne) → Routes 6-11 → Rock Tunnel (needs Flash) → Lavender → Celadon (**Erika**, Rocket Hideout) → Saffron (**Sabrina**, after Silph Co.) → Fuchsia (**Koga**, Safari Zone for HMs) → Routes 12-15 → Cinnabar (**Blaine**, needs Surf) → back to Viridian (**Giovanni**) → Victory Road → Elite Four.

## Optional pickups worth knowing
- **Magikarp salesman** — in the **Route 4 / Mt Moon Pokémon Center** a man sells a **Magikarp for $500**. Magikarp is near-useless early (Splash/Tackle) but evolves into **Gyarados** (Water/Flying, 125 Atk / 95 HP) at **L20** — a top-tier attacker. Rule of thumb: **skip it if you chose Squirtle** (you already have Water); **grab it with Charmander or Bulbasaur** if you can spare the $500, since it's your best early Water option. Caveat: Gyarados is **4× weak to Electric** and 2× to Rock.

## HMs you'll need (and where they gate progress)
- **HM01 Cut** — from S.S. Anne (Vermilion); clears the tree blocking Route 9/Surge area and bushes.
- **HM03 Surf** — Safari Zone (Fuchsia); required for Cinnabar/Blaine and much of the late map.
- **HM04 Strength** — Fuchsia; boulders.
- **HM05 Flash** — Route 2 (after Pewter, needs 10 caught… but only Pikachu caught this run — Flash may be skippable if you tough out dark Rock Tunnel).

## Type quick-reference (what our two attackers hit)
| Attack | 2× (super) | 0.5× (resisted) | 0× |
|---|---|---|---|
| **Ember/Fire** | Grass, Bug, Ice | Fire, Water, Rock, Dragon | — |
| **ThunderShock/Electric** | Water, Flying | Grass, Electric, Dragon | **Ground** |

Full chart in `assets/json/typechart.json`; movesets/learnsets in `assets/json/pokemon.json`. Notable pickups for this team: **Thunderbolt (TM24, from Surge)** on Pikachu, and Pikachu naturally learns **Quick Attack @L16**.

*This document is original analysis of the game's own data files, not a copy of any third-party walkthrough.*
