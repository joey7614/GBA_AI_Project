# OpenClaw — pokeemerald Source-Based Approach
# NEW DIRECTION: Modify C source, build custom ROM, proper AI API

---

## STATUS (session ending)
- All previous Lua VRAM/tilemap injection experiments are ABANDONED
- User cloned `pokeemerald` (decompiled Emerald source) into this repo
- Path: `c:/Users/JL/Documents/GBA_AI_Project/pokeemerald/`
- Goal: add AI dialog API directly in C source, rebuild ROM

---

## Why this approach is better
Old approach (Lua tilemap write) was blocked because:
- Text rendering is synchronous (done before gTasks frame callback)
- Tilemap writes to border tiles (t547/t548) had no visual effect on text characters
- Font tile pixel data writes untested (next step was step3q, but abandoned)

New approach: **modify the C source → clean hooks → rebuild ROM → load in mGBA**
- We own the code, so we can add any hook/buffer/flag we want
- No memory address guessing needed (use symbol table or linker script)
- Clean IPC: game writes request to known EWRAM address, Python responds

---

## pokeemerald Repo Layout (key files only)

```
pokeemerald/
├── src/
│   ├── field_message_box.c   ← MAIN HOOK TARGET (ShowFieldMessage)
│   ├── scrcmd.c              ← Script commands (ScrCmd_message, opcode 0x67)
│   ├── string_util.c         ← gStringVar4 buffer (1000 bytes), StringExpandPlaceholders
│   ├── text.c                ← Low-level TextPrinter
│   ├── menu.c                ← AddTextPrinterForMessage (uses gStringVar4)
│   ├── task.c                ← gTasks system (CreateTask, RunTasks, FindTaskIdByFunc)
│   ├── script.c              ← Script bytecode interpreter (RunScriptCommand)
│   └── overworld.c           ← Game loop: ScriptContext_RunScript + RunTasks every frame
├── include/
│   ├── task.h                ← struct Task { TaskFunc func; bool8 isActive; s16 data[16]; }
│   ├── script.h              ← struct ScriptContext { scriptPtr; stack[20]; data[4]; }
│   └── string_util.h         ← extern gStringVar4 declaration
├── data/
│   ├── script_cmd_table.inc  ← opcode 0x67 = msgbox = ScrCmd_message
│   └── maps/*/scripts.inc    ← NPC scripts with msgbox Text_Label
└── Makefile                  ← arm-none-eabi-gcc, `make` builds pokeemerald.gba
```

---

## Key Functions & Addresses

### Dialog Flow (message_box path)
```
NPC script: msgbox Text_Label, MSGBOX_DEFAULT
  → ScrCmd_message()           [scrcmd.c:1265]
      reads ptr from ROM bytecode
      calls ShowFieldMessage(ptr)
  → ShowFieldMessage(str)      [field_message_box.c:62]  ← BEST HOOK POINT
      calls ExpandStringAndStartDrawFieldMessage(str, TRUE)
        calls StringExpandPlaceholders(gStringVar4, str) [string_util.c:335]
        calls AddTextPrinterForMessage(TRUE)             [menu.c:191]
          uses gStringVar4 as the displayed text
```

### gStringVar4 — THE TEXT BUFFER
```c
// string_util.c:9
EWRAM_DATA u8 gStringVar4[0x3E8] = {0};  // 1000 bytes in EWRAM
```
- This is what gets DISPLAYED in the dialog box
- Writing AI text here (before AddTextPrinterForMessage is called) = AI dialog shown
- Python writes to this EWRAM address via mGBA Lua IPC

### gTasks — Task System
```c
// include/task.h
struct Task {
    TaskFunc func;       // 4 bytes (function pointer)
    bool8 isActive;      // 1 byte  (+4)
    u8 prev, next, priority;
    s16 data[NUM_TASK_DATA];  // 16 × 2 = 32 bytes
};
// Total: 40 bytes per task, 16 slots = 640 bytes starting at gTasks (0x03005E00 confirmed)
```

---

## Proposed Architecture: Custom AI IPC Buffer

### Add to EWRAM (new file: src/openclaw_ai.c)
```c
// Custom IPC buffer at a known EWRAM location
struct OpenClawIPC {
    u8  request_ready;    // 1 = new NPC dialog triggered, Python should respond
    u8  response_ready;   // 1 = Python wrote AI response to ai_text buffer
    u8  npc_id;           // which NPC triggered dialog
    u8  map_num;          // current map
    u8  map_group;        // current map group
    u8  pad[3];
    u8  ai_text[256];     // AI response in Gen3 encoding (written by Python)
    u8  npc_orig_text[256]; // Original NPC text (copied from ROM, decoded)
};
EWRAM_DATA struct OpenClawIPC gOpenClawIPC = {0};
```

### Hook ShowFieldMessage() (field_message_box.c)
```c
bool8 ShowFieldMessage(const u8 *str)
{
    if (sFieldMessageBoxMode != FIELD_MESSAGE_BOX_HIDDEN)
        return FALSE;

    // ── AI HOOK ──────────────────────────────────────
    // Copy original text for context, set request flag
    StringCopy(gOpenClawIPC.npc_orig_text, str);
    gOpenClawIPC.map_num   = gSaveBlock1Ptr->location.mapNum;
    gOpenClawIPC.map_group = gSaveBlock1Ptr->location.mapGroup;
    gOpenClawIPC.request_ready  = 1;
    gOpenClawIPC.response_ready = 0;

    // Wait for Python to fill ai_text and set response_ready = 1
    // (handled by Task_OpenClawWaitForAI below)
    CreateTask(Task_OpenClawWaitForAI, 0x50);
    return TRUE;    // defer showing until AI responds
    // ─────────────────────────────────────────────────
}

// New task: polls response_ready each frame
void Task_OpenClawWaitForAI(u8 taskId)
{
    if (gOpenClawIPC.response_ready) {
        gOpenClawIPC.request_ready  = 0;
        gOpenClawIPC.response_ready = 0;
        // Show AI text instead of original
        ExpandStringAndStartDrawFieldMessage(gOpenClawIPC.ai_text, TRUE);
        sFieldMessageBoxMode = FIELD_MESSAGE_BOX_NORMAL;
        DestroyTask(taskId);
    }
    // else: keep waiting (called next frame)
}
```

### Python/Lua side (mGBA bridge)
```lua
-- bridge.lua: poll gOpenClawIPC.request_ready every frame
local IPC_BASE = ???   -- determined from ELF symbol table after build
-- When request_ready == 1:
--   read npc_orig_text, map_num, map_group
--   write to ipc/trigger.json
--   Python calls Gemini, gets response
--   Python encodes response to Gen3 charset
--   Python writes encoded bytes to IPC_BASE + offset (ai_text)
--   Python writes 1 to IPC_BASE + response_ready offset
```

---

## Gen3 Character Encoding (for encoding AI response)
```
A-Z : 0xBB - 0xD4
a-z : 0xD5 - 0xEE
0-9 : 0xA1 - 0xAA
' ' : 0xAB (space)
'.' : 0xAD
',' : 0xB8
'!' : 0xAC
'?' : 0xAD (check)
'\n': 0xFE (line break)
'\p': 0xFF (paragraph / wait for A)
'$' : 0xFF (EOS - end of string marker)
```
Note: verify exact values from `constants/characters.inc` in pokeemerald.

---

## Build Instructions
```bash
cd pokeemerald/

# Install dependencies (if not done):
# arm-none-eabi-gcc, devkitARM, or agbcc
# See: https://github.com/pret/pokeemerald/blob/master/INSTALL.md

# Build:
make -j4

# Output: pokeemerald.gba
# Load in mGBA, then attach bridge.lua
```

---

## IPC Buffer Address — How to Find
After `make`, use:
```bash
arm-none-eabi-nm pokeemerald.elf | grep gOpenClawIPC
# or
arm-none-eabi-nm pokeemerald.elf | grep gStringVar4
```
These are in EWRAM (0x02xxxxxx range). Hardcode the address in bridge.lua.

---

## Next Steps for Next Session
1. Set up build environment for pokeemerald (check INSTALL.md)
2. Verify `make` produces `pokeemerald.gba` successfully (no AI changes yet)
3. Find `gStringVar4` EWRAM address from ELF symbols
4. Add `src/openclaw_ai.c` + `include/openclaw_ai.h`
5. Modify `field_message_box.c`: hook ShowFieldMessage()
6. Rebuild, load in mGBA, confirm dialog still works
7. Update `bridge.lua` to poll the IPC buffer instead of gTasks
8. Test end-to-end: NPC dialog → Lua detects → Python calls Gemini → AI text shown

---

## Previous Lua Diagnostic Findings (archive, no longer needed)
- gTasks base: 0x03005E00, DIALOG_FUNC: 0x08098155 (opcode scan confirmed)
- BG1: char_block=0, screen_block=29 (0x0600E800)
- VRAM writable via emu:write8/16
- Text rendering is synchronous (done before gTasks callback fires)
- Tilemap write succeeded in memory but no visual effect (target was border tiles)
- Font tiles 512+ are pre-loaded, don't change between dialogs
- All step3a–step3q Lua scripts in lua_tests/ kept for reference
