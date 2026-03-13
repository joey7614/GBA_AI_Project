# OpenClaw — MVP Work Plan
# Goal: AI Agent controlling NPC dialog and movement in Pokemon FireRed

---

## Architecture

```
[pokefirered ROM] <--EWRAM--> [Lua bridge in mGBA] <--file--> [Python + Gemini]
```

---

## Step 1 — Clean Build of pokefirered ✅

- [x] Install build dependencies — WSL (Ubuntu), `build-essential`, `binutils-arm-none-eabi`, `libpng-dev`
- [x] Clone and build `agbcc` (pret's custom GBA compiler), install into pokefirered
- [x] Run `make -j$(nproc)` in `pokefirered/` via WSL
- [x] `pokefirered.gba` and `pokefirered.elf` produced successfully
- [ ] Load `pokefirered.gba` in mGBA, confirm game boots

**Notes:**
- Build toolchain: agbcc (default) via WSL Ubuntu
- EWRAM usage: 261040 / 262144 bytes (99.58%) — only **1104 bytes free**
- IPC struct must stay under ~1000 bytes — use 128-byte text buffers instead of 256

---

## Step 2 — Add IPC Struct + Hook ShowFieldMessage ✅

### 2a — Create IPC files ✅
- [x] Created `pokefirered/include/openclaw_ai.h`
  - `struct OpenClawIPC`: `request_ready`, `response_ready`, `pad[2]`, `npc_orig_text[128]`, `ai_text[128]`
  - `OPENCLAW_TEXT_SIZE = 128` (kept small due to EWRAM constraints)
- [x] Created `pokefirered/src/openclaw_ai.c`
  - `EWRAM_DATA struct OpenClawIPC gOpenClawIPC = {0};`
  - Auto-included by Makefile wildcard (no Makefile edit needed)

### 2b — Hook ShowFieldMessage ✅
- [x] Edited `pokefirered/src/field_message_box.c:ShowFieldMessage()`
  - Copies `str` → `gOpenClawIPC.npc_orig_text` via `StringCopyN`
  - Sets `request_ready = 1`
  - Spawns `Task_OpenClawWaitForAI` instead of rendering immediately

### 2c — Add wait task ✅
- [x] Added `Task_OpenClawWaitForAI` in `field_message_box.c`
  - Polls `response_ready` each frame
  - When 1: copies `ai_text` → `gStringVar4`, calls `StartDrawFieldMessageBox()`, destroys self

### 2d — Verify with hardcoded response ✅
- [x] Hardcoded Gen3-encoded `"Hello from AI!"` as temp test response
- [x] Rebuilt — EWRAM: 261300 B / 262144 B (260 bytes added for IPC struct)
- [x] Loaded in mGBA — dialog shows `"Hello from AI!"` correctly
- [x] Hook confirmed working

**Key facts:**
- `gOpenClawIPC` address: **`0x0203F468`**
- IPC struct size: 260 bytes (1104 → 844 bytes remaining in EWRAM)
- Gen3 encoding verified: A=0xBB, a=0xD5, space=0x00, EOS=0xFF

---

## Step 2e — Agent Toolbox ✅

- [x] Created `agent_tools/` directory for Gemini agent function calling tools
- [x] `agent_tools/gen3_encode.py` — `encode_text` tool with Gemini function declaration + handler
- [x] `agent_tools/__init__.py` — `ALL_TOOLS` list for registering tools with Gemini
- [x] Created `build.bat` — double-click to build ROM from Windows

**Architecture note:** This is a Gemini **agent** (function calling), not a simple prompt/response.
Each game action (display dialog, move NPC, etc.) is a tool the agent can invoke.

---

## Step 3 — Find IPC Address ✅

- [x] `arm-none-eabi-nm pokefirered.elf | grep gOpenClawIPC` → **`0x0203F468`**
- [x] Field offsets:
  - `+0` `request_ready`
  - `+1` `response_ready`
  - `+2` `pad[2]`
  - `+4` `npc_orig_text[128]`
  - `+132` `ai_text[128]`

---

## Step 3.5 — Data Architecture

### Entity Types / Autonomy Levels

| Type | Movement | AI loop | Memory | Token cost/hr |
|------|----------|---------|--------|---------------|
| **Static NPC** (nurse, shopkeeper) | None — stays at post | Dialog only, on demand | Warm | ~50 (rare talk) |
| **Roaming NPC** (villager, fisherman) | Walks own area | Periodic tick on map | Warm | ~200 |
| **Adventuring NPC** (aspiring trainer) | Travels between maps | Goal-based, decision points | Hot+Warm+Cold | ~1000 |
| **Companion** (bonded, follows player) | Follows + AI free moments | Always-on + event-driven | Hot+Warm+Cold chapters | ~600 + catches |
| **Pokemon** (companion's party) | N/A (in party) | None — companion AI decides | Warm (bond) | 0 |

**Key rule:** Off-screen events are simulated (coin flip outcome), not executed. Only player-visible events run full AI.

### Tiered Memory Model

```
HOT   (~100 tokens) — always in context
  → persona seed, current state, emotional state, bond/affinity level

WARM  (~300 tokens) — loaded on interaction
  → last 5-10 interactions, active storyline flags, current arc

COLD  (~200 tokens total) — journey chapter summaries, never raw
  → "Ch1: Met in Pallet. You picked Charmander. I warned you about Brock."
  → "Ch2: Beat Brock together. Charmander nearly fainted. Bond grew."
  → compressed by AI every ~10 warm interactions

ARCHIVE — DB only, never loaded into context
  → full raw event log
```

**Token budget per interaction:**
- Regular NPC: ~265 tokens (HOT + WARM)
- Companion: ~600 tokens (HOT + WARM + COLD summaries)
- Regardless of journey length — COLD stays bounded

### Companion — "Journey NPC"

When player says "I want you to come with me":
- Entity type promoted to `companion`
- Physical: follower event object teleports to player on every map change
- Awareness: receives ALL passive game events (map changes, battles, faints)
- Memory: chapter-based so journey length never blows token budget

Passive events emitted by Lua bridge (not just dialog):
```json
{ "trigger": "map_change", "map_name": "Cerulean City" }
{ "trigger": "battle_won", "opponent": "Misty", "player_hp_remaining": 12 }
{ "trigger": "pokemon_fainted", "pokemon": "Charmander" }
```

### Companion Movement Modes

```
FOLLOW MODE (default, 0 tokens)
  Lua script walks companion toward player every N steps.
  No AI involved. Pure game engine behavior.

AI FREE MODE (event-triggered, ~150 tokens, fires once)
  AI generates a short movement sequence, ROM executes it, returns to FOLLOW MODE.
  Triggers:
    - Player stops for >10 seconds
    - Player enters a new area
    - Notable event (battle won, item found, rare Pokemon nearby)
    - Random "life moment" timer
  Examples:
    - Player stops → companion wanders to bush, looks around, walks back
    - Player enters Cerulean → companion runs ahead to look at the gym
    - Player wins hard battle → companion jumps around celebrating
```

AI pays tokens only at decision moment, not during execution.

### Companion as Parallel Trainer

Companion has its own team of up to 6 Pokémon and grows alongside the player:

**Level pacing:**
- Lua bridge reads player's party levels periodically
- Python calculates player's average level
- Companion's team is kept at similar level (slightly behind for balance)
- Level adjustment written directly to companion's party data in game memory

**Map-aware catching:**
- Wild encounter tables extracted from pokefirered source (`data/wild_pokemon.c`)
- Static lookup built: `map_id → [possible wild Pokemon species]`
- On every map change, Python checks: does companion want something here?
- If yes + party has space → AI triggers catch attempt event

**AI catch decision loop:**
```
Enter new map
  → check map's wild Pokemon list
  → check companion's wishlist + current team gaps
  → if desired Pokemon available: trigger catch event
  → update wishlist based on upcoming gyms / strategy
```

**AI team strategy:**
- Companion knows upcoming gym types (Fire Red gym order is fixed)
- Builds team to counter next gym ("Need a water type for Cinnabar")
- Personality influences choices ("I like cute Pokémon" vs "I want raw power")
- Companion bond with each individual Pokémon tracked separately

### Companion Inventory & Shopping

Companion carries a small inventory — Pokéballs, potions, and basic items.

**Inventory rules:**
- Max ~10 item slots (small backpack, not full bag)
- Tracked in DB, synced to game memory when companion is active
- Companion monitors its own supply before leaving a town

**Autonomous shopping behavior:**
- On entering a city/town with a Pokémart, companion checks:
  - Is Pokéball count low? (< 5 before a route with wanted Pokémon)
  - Are potions low? (< 3 before a tough gym)
- If yes → companion tells player: *"Wait for me, I need to grab some things before we leave."*
- Companion walks to Pokémart, "buys" items (Python writes to inventory DB + game memory)
- Companion returns to player

**World knowledge needed:**
- Static lookup: `town → has_pokemart, items_available, item_prices`
- Extracted once from pokefirered source (`data/pokemart.c` / shop scripts)
- Companion uses this to plan purchases before leaving towns

**DB additions for companion inventory:**
```sql
companion_inventory(
  id, companion_id,
  item_id INTEGER,         -- matches game ITEM_* constants
  item_name TEXT,          -- "Poke Ball", "Potion"
  quantity INTEGER
)

town_shops(
  map_group, map_num,
  item_id INTEGER,
  price INTEGER            -- extracted from pokefirered source once
)
```

**DB additions for companion team:**
```sql
companion_pokemon(
  id, companion_id,
  species_id INTEGER,
  nickname TEXT,
  level INTEGER,
  moves TEXT,              -- JSON: ["Tackle", "Growl", ...]
  bond_with_companion INTEGER  -- 0-100, companion cares about its own Pokemon
)

companion_wishlist(
  id, companion_id,
  species_id INTEGER,
  reason TEXT,             -- "Need water type for Misty"
  priority INTEGER         -- 1=urgent, 3=nice to have
)

map_wild_encounters(
  map_group, map_num,
  species_id INTEGER,
  encounter_rate INTEGER   -- extracted from pokefirered source once
)
```

### Database Schema

```sql
entities(
  id, type,                    -- 'npc'|'rival'|'companion'|'pokemon'
  map_group, map_num,          -- NULL = mobile/follows player
  persona_seed TEXT,           -- "Grumpy fisherman who lost his Magikarp" (50 tokens max)
  current_state TEXT,          -- 'idle'|'dialog'|'battle'|'trade'|'following'
  emotional_state TEXT,        -- 'happy'|'worried'|'angry'|'neutral'
  bond_level INTEGER           -- 0-100
)

memories(
  id, entity_id,
  tier TEXT,                   -- 'warm'|'cold'
  content TEXT,
  relevance_tags TEXT,         -- "battle,cerulean,charmander" for filtering
  created_at DATETIME
)

journey_chapters(
  id, entity_id,
  chapter_num INTEGER,
  summary TEXT,                -- 1-2 sentence AI-generated compression
  created_at DATETIME
)

world_events(
  flag_id INTEGER,             -- matches game FLAG_* constants
  description TEXT,            -- "Player defeated Brock"
  timestamp DATETIME
)

relationships(
  entity_a, entity_b,
  affinity INTEGER,            -- -100 to 100
  last_seen DATETIME
)
```

### Scoped Loading

```python
# Only NPCs on current map are "awake" — others cost zero tokens
active_entities = db.query(
    "SELECT * FROM entities WHERE map_group=? AND map_num=?",
    (map_group, map_num)
)
# Companions always included regardless of map
```

### Memory Compression

Every 10 warm memories → AI compresses → 1 cold chapter summary → warm cleared.
Old memories never grow context. Journey can be 100 hours long, COLD stays ~200 tokens.

---

## Step 4 — Lua Bridge in mGBA (`bridge.lua`) ✅

- [x] Created `bridge.lua` in project root
- [x] Frame callback polls `request_ready` at `IPC_BASE + 0`
  - Reads `npc_orig_text` bytes from `IPC_BASE + 4` until `0xFF`
  - Writes `ipc/request.json` with `{ npc_orig_bytes, map_group, map_num }`
  - Clears `request_ready` to 0
- [x] Polls for `ipc/response.json` each frame
  - Reads `{"ai_bytes": [...]}` from file
  - Writes Gen3 bytes to `IPC_BASE + 132` (ai_text)
  - Sets `response_ready = 1` at `IPC_BASE + 1`
  - Deletes `response.json`
- [x] 10-second timeout with console log
- [x] `Task_OpenClawWaitForAI` updated: polls `response_ready`, copies `ai_text` → `gStringVar4`
- [x] ROM rebuilt successfully (EWRAM 261300 B / 256 KB)
- [ ] Load script in mGBA: Tools → Scripting → Load Script
- [ ] Confirm Lua detects NPC trigger (log to console)

**IPC offsets:**
- `IPC_BASE + 0`   → `request_ready`
- `IPC_BASE + 1`   → `response_ready`
- `IPC_BASE + 4`   → `npc_orig_text[128]`
- `IPC_BASE + 132` → `ai_text[128]`

---

## Step 5 — Python + Gemini (`main.py`) ✅

- [x] Watches `ipc/request.json` every 50ms
- [x] Reads `npc_orig_bytes` (Gen3), decodes to ASCII for prompt
- [x] Builds Gemini prompt with orig text + affinity + past memories
- [x] Calls Gemini, truncates response to 60 chars
- [x] Encodes response to Gen3 bytes via `agent_tools.encode_text`
- [x] Writes `ipc/response.json` with `{"ai_bytes": [...]}`
- [x] Saves interaction to SQLite (npc_memory + npc_affinity tables)
- [ ] Confirm end-to-end round-trip works

---

## Step 6 — End-to-End Test ✅

- [x] mGBA running `pokefirered.gba` + `bridge.lua`
- [x] `python main.py` watching ipc/ for requests
- [x] Talk to NPC → Lua writes `request.json` → Python calls Gemini → writes `response.json` → Lua injects bytes → ROM shows AI dialog
- [x] Player stays locked during AI wait (sMessageBoxType = NORMAL)
- [x] 10-second timeout → falls back to original NPC text
- [x] SKIP_AI_MAPS config for bypassing story NPCs
- [x] Prompt improved: character name extracted, bond level context, natural language encouraged
- [x] API key working (Gemini 2.5 Flash)

**MVP COMPLETE** — AI-controlled NPC dialog is live in Pokemon FireRed.

---

## File Structure (end of MVP)

```
GBA_AI_Project/
├── pokefirered/
│   ├── src/
│   │   ├── field_message_box.c   ← hooked
│   │   └── openclaw_ai.c         ← NEW: IPC struct + wait task
│   └── include/
│       └── openclaw_ai.h         ← NEW: IPC struct definition
├── bridge.lua                    ← mGBA Lua bridge
├── main.py                       ← Python + Gemini
├── ipc/
│   ├── request.json              ← Lua writes, Python reads
│   └── response.json             ← Python writes, Lua reads
├── POKEFIRERED_APPROACH.md
├── POKEEMERALD_APPROACH.md
└── work.md                       ← this file
```

---

## File Structure (current)

```
GBA_AI_Project/
├── pokefirered/
│   ├── src/
│   │   ├── field_message_box.c   ← hooked (ShowFieldMessage + WaitForAI task)
│   │   └── openclaw_ai.c         ← NEW: IPC struct in EWRAM
│   └── include/
│       └── openclaw_ai.h         ← NEW: IPC struct definition
├── agent_tools/
│   ├── __init__.py               ← ALL_TOOLS registry
│   └── gen3_encode.py            ← encode_text Gemini tool
├── bridge.lua                    ← mGBA Lua bridge (TODO)
├── main.py                       ← Python + Gemini agent (TODO: rewire to agent)
├── build.bat                     ← Windows one-click build script
├── ipc/                          ← runtime IPC files (TODO)
├── POKEFIRERED_APPROACH.md
└── work.md
```

---

## Later Implementation — Game Mechanics Rules

These features are **not MVP**. Document here for future implementation.

### Companion / Rival Relationship System

**If player leaves companion without waiting:**
- **High bond** → companion catches up and continues helping the player
- **Low bond** → companion becomes a rival

### Companion Battle Rules (purely for fun, no stakes)

- No money penalty for either side
- No exp penalty for either side
- **Winner pays loser one Revival Potion** — ensures loser always has at least 1 Pokémon alive in game

### Rival Battle Rules (real stakes)

- Loser loses money
- Loser loses **5% exp** on every Pokémon that battled (not just the ones that fainted)
- **Floor: 0 exp at current level** — can never de-level, minimum is 0 exp for current level

---

## Long-Term Vision

MVP is just the start. The end goal is a **full AI rival player** living inside the game world:

| Feature | What it means |
|---------|---------------|
| Dynamic dialog | NPCs respond uniquely to the player |
| Personality | Each NPC/rival has persistent traits and memory |
| Catch Pokémon | AI rival uses the game's catch mechanics to build a real team |
| Leveling | AI rival's Pokémon grow over time through battles |
| Trade / Exchange | AI rival can trade Pokémon with the player or other NPCs |
| Strategic play | AI rival makes decisions like a real player, not scripted |

This requires extending the agent toolbox over time:
- `display_message` — dialog (MVP)
- `move_npc` — walk the rival around the map
- `initiate_trade` — open trade screen with player
- `catch_pokemon` — trigger catch attempt
- `select_move` — choose battle move
- `read_party` — inspect rival's current team
- `read_player_party` — observe the player's team

The Gemini agent orchestrates all of these tools with memory and personality context.

---

## Current Status

- [x] pokefirered repo cloned and analyzed
- [x] POKEFIRERED_APPROACH.md written
- [x] Step 1: Clean build ✅
- [x] Step 2: IPC struct + ShowFieldMessage hook ✅
- [x] Step 2e: Agent toolbox (`agent_tools/`) ✅
- [x] Step 3: IPC address found (`0x0203F468`) ✅
- [ ] Step 3.5: Data architecture + DB schema (entities, memories, chapters, world_events)
- [x] Step 4: Lua bridge (`bridge.lua`) — reads EWRAM, writes IPC files ✅
- [x] Step 5: Rewire `main.py` as proper Gemini agent with tool calling ✅
- [x] Step 6: End-to-end test ✅ — MVP complete!
