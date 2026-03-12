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

## Step 4 — Lua Bridge in mGBA (`bridge.lua`)

- [ ] Create `bridge.lua` in project root
- [ ] Every frame callback:
  - Read `request_ready` from `IPC_BASE + 0`
  - If 1:
    - Read `npc_orig_text` bytes from `IPC_BASE + 8` until `0xFF`
    - Write `ipc/request.json` with `{ text, map_num, map_group, npc_id }`
    - Clear `request_ready` to 0
  - Check if `ipc/response.json` exists
  - If yes:
    - Read encoded Gen3 bytes from file
    - Write bytes to `IPC_BASE + 264` (ai_text)
    - Set `response_ready = 1` at `IPC_BASE + 1`
    - Delete `ipc/response.json`
- [ ] Load script in mGBA: Tools → Scripting → Load Script
- [ ] Confirm Lua detects NPC trigger (log to console)

---

## Step 5 — Python + Gemini (`main.py`)

- [ ] Watch `ipc/request.json` for new file
- [ ] On trigger:
  - Read NPC original text
  - Build Gemini prompt:
    ```
    You are an NPC in Pokemon FireRed.
    Your original line was: "{text}"
    Reply in character, max 2 sentences.
    ```
  - Call Gemini API, get response string
- [ ] Encode response to Gen3 charset:
  ```
  A-Z  → 0xBB–0xD4
  a-z  → 0xD5–0xEE
  0-9  → 0xA1–0xAA
  ' '  → 0x00
  '.'  → 0xAD
  ','  → 0xB8
  '!'  → 0xAB
  '?'  → 0xAC
  '\n' → 0xFE
  EOS  → 0xFF
  ```
- [ ] Write encoded bytes to `ipc/response.json`
- [ ] Confirm Python round-trip works (request in → response out)

---

## Step 6 — End-to-End Test

- [ ] Run mGBA with `pokefirered.gba` + `bridge.lua` loaded
- [ ] Run `python main.py` in background
- [ ] Walk up to NPC, press A
- [ ] Confirm:
  - Lua detects trigger, writes `request.json`
  - Python calls Gemini, writes `response.json`
  - Lua writes response to EWRAM
  - ROM displays AI text in dialog box
- [ ] Test multiple NPCs
- [ ] Test edge cases: long responses (truncate at 255 bytes), unsupported characters

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
- [ ] Step 4: Lua bridge (`bridge.lua`) — reads EWRAM, writes IPC files
- [ ] Step 5: Rewire `main.py` as proper Gemini agent with tool calling
- [ ] Step 6: End-to-end test
