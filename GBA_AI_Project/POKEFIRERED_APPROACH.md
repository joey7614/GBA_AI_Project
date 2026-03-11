# OpenClaw — pokefirered Source-Based Approach
# Modify C source, build custom ROM, proper AI API

---

## Overview

`pokefirered` is a full decompilation of Pokémon FireRed/LeafGreen for the GBA.
The architecture is nearly identical to pokeemerald — same task system, same
dialog flow, same Gen3 character encoding. All hooks apply directly.

Builds:
- `pokefirered.gba`  sha1: `41cb23d8dccc8ebd7c649cd8fbb58eeace6e2fdc`
- `pokeleafgreen.gba` sha1: `574fa542ffebb14be69902d1d36f1ec0a4afd71e`

---

## Repo Layout (key files only)

```
pokefirered/
├── src/
│   ├── field_message_box.c   ← MAIN HOOK TARGET (ShowFieldMessage)
│   ├── scrcmd.c              ← Script commands (ScrCmd_message line 1263)
│   ├── string_util.c         ← gStringVar4 buffer (1000 bytes EWRAM, line 7)
│   ├── text.c                ← Low-level font rendering, 8 font types
│   ├── text_printer.c        ← 32 concurrent text printers, glyph decompression
│   ├── task.c                ← gTasks system (CreateTask, RunTasks, FindTaskIdByFunc)
│   ├── script.c              ← Script bytecode interpreter
│   ├── overworld.c           ← Game loop, imports all field systems
│   ├── field_specials.c      ← ShowFieldMessageStringVar4() — prints gStringVar4 directly
│   └── save.c                ← Flash sector-based save (14 sectors/slot)
├── include/
│   ├── task.h                ← struct Task { func; isActive; prev/next/priority; s16 data[16]; }
│   ├── script.h              ← struct ScriptContext { scriptPtr; stack[20]; data[4]; }
│   ├── string_util.h         ← extern gStringVar4 declaration
│   ├── field_message_box.h   ← HIDDEN / NORMAL / AUTO_SCROLL states
│   └── characters.h          ← Full Gen3 character encoding table
├── data/
│   ├── script_cmd_table.inc  ← 100+ opcode → C function mappings
│   ├── event_scripts.s       ← NPC/event script bytecode (~55 KB)
│   ├── maps/                 ← 200+ map directories
│   └── text/                 ← All dialog string .inc files
├── constants/
│   └── (gba_constants, misc_constants, version, m4a_constants)
└── Makefile                  ← arm-none-eabi-gcc, outputs pokefirered.gba + .elf + .map
```

---

## Dialog Flow (message box path)

```
NPC script: msgbox Text_Label
  → ScrCmd_message()                   [scrcmd.c:1263]
      reads ptr from ROM bytecode
      calls ShowFieldMessage(ptr)
  → ShowFieldMessage(str)              [field_message_box.c:65]  ← BEST HOOK POINT
      calls ExpandStringAndStartDrawFieldMessageBox(str)
        calls StringExpandPlaceholders(gStringVar4, str)    [string_util.c]
        calls AddTextPrinterDiffStyle(TRUE)                 [text_printer.c]
        calls CreateTask(Task_DrawFieldMessageBox, 80)
  → Task_DrawFieldMessageBox (task)    [field_message_box.c:22]
      case 0: LoadStdWindowFrameGfx()
      case 1: DrawDialogueFrame(0, TRUE)
      case 2: RunTextPrinters_CheckPrinter0Active() → destroy task when done
```

Key difference from pokeemerald:
- pokeemerald calls `AddTextPrinterForMessage(TRUE)` → pokefirered calls `AddTextPrinterDiffStyle(TRUE)`
- pokefirered has `ShowFieldMessageStringVar4()` in `field_specials.c:120` — a helper that directly passes `gStringVar4` to `ShowFieldMessage`. Useful for AI hook: write AI text into `gStringVar4`, call this function.

---

## gStringVar4 — THE TEXT BUFFER

```c
// string_util.c:7
EWRAM_DATA u8 gStringVar4[1000] = {};   // 1000 bytes in EWRAM
```

- This is the expanded string displayed in the dialog box
- `StringExpandPlaceholders(gStringVar4, str)` copies + expands ROM string into it
- Writing AI text here before `AddTextPrinterDiffStyle` is called → AI text shown
- Python writes to this EWRAM address via mGBA Lua IPC

---

## Task System

```c
// include/task.h
struct Task {
    TaskFunc func;          // 4 bytes — function pointer
    bool8 isActive;         // 1 byte  (+4)
    u8 prev, next, priority; // linked list
    s16 data[16];           // 32 bytes of task-local storage
};
// NUM_TASKS = 16 slots, linked list ordered by priority
```

Same as pokeemerald. `Task_DrawFieldMessageBox` uses priority 80.

---

## Gen3 Character Encoding (from include/characters.h — CONFIRMED)

```
CHAR_SPACE         0x00
A–Z                0xBB – 0xD4
a–z                0xD5 – 0xEE
0–9                0xA1 – 0xAA  (CHAR_0=0xA1 ... CHAR_9=0xAA)
'!'                0xAB
'?'                0xAC
'.'                0xAD
'-'                0xAE  (hyphen)
','                0xB8
':'                0xF0
'\n' (newline)     0xFE  (CHAR_NEWLINE)
EOS (end of str)   0xFF  (EOS)
0xFA               CHAR_PROMPT_SCROLL — wait A, scroll
0xFB               CHAR_PROMPT_CLEAR  — wait A, clear
0xFC               EXT_CTRL_CODE_BEGIN — extended control sequence follows
0xFD               PLACEHOLDER_BEGIN
```

**Important difference from pokeemerald docs:**
- SPACE is `0x00` (not `0xAB` as noted in pokeemerald docs — that was `!`)
- Verify pokeemerald's SPACE value from its own characters.inc

---

## Proposed Architecture: Custom AI IPC Buffer

### Add to EWRAM (new file: src/openclaw_ai.c)

```c
struct OpenClawIPC {
    u8  request_ready;      // 1 = NPC dialog triggered, Python should respond
    u8  response_ready;     // 1 = Python wrote AI response to ai_text
    u8  npc_id;             // which NPC triggered dialog
    u8  map_num;            // gSaveBlock1Ptr->location.mapNum
    u8  map_group;          // gSaveBlock1Ptr->location.mapGroup
    u8  pad[3];
    u8  ai_text[256];       // AI response in Gen3 encoding (written by Python)
    u8  npc_orig_text[256]; // Original NPC text (copied from ROM)
};
EWRAM_DATA struct OpenClawIPC gOpenClawIPC = {0};
```

### Hook ShowFieldMessage() (field_message_box.c:65)

```c
bool8 ShowFieldMessage(const u8 *str)
{
    if (sMessageBoxType != FIELD_MESSAGE_BOX_HIDDEN)
        return FALSE;

    // ── AI HOOK ──────────────────────────────────────
    StringCopy(gOpenClawIPC.npc_orig_text, str);
    gOpenClawIPC.map_num   = gSaveBlock1Ptr->location.mapNum;
    gOpenClawIPC.map_group = gSaveBlock1Ptr->location.mapGroup;
    gOpenClawIPC.request_ready  = 1;
    gOpenClawIPC.response_ready = 0;
    CreateTask(Task_OpenClawWaitForAI, 0x50);
    sMessageBoxType = FIELD_MESSAGE_BOX_NORMAL;
    return TRUE;
    // ─────────────────────────────────────────────────
}

void Task_OpenClawWaitForAI(u8 taskId)
{
    if (gOpenClawIPC.response_ready) {
        gOpenClawIPC.request_ready  = 0;
        gOpenClawIPC.response_ready = 0;
        // Write AI text into gStringVar4, then render it
        StringCopy(gStringVar4, gOpenClawIPC.ai_text);
        AddTextPrinterDiffStyle(TRUE);           // pokefirered-specific
        CreateTask(Task_DrawFieldMessageBox, 80);
        DestroyTask(taskId);
    }
}
```

### Alternative hook — use ShowFieldMessageStringVar4()

`field_specials.c:120` already has:
```c
void ShowFieldMessageStringVar4(void) {
    ShowFieldMessage(gStringVar4);
}
```

You can write AI text into `gStringVar4` via Lua/Python, then call this function.
This avoids modifying `ShowFieldMessage` itself.

### Python/Lua bridge side

```lua
-- bridge.lua: poll gOpenClawIPC.request_ready every frame
local IPC_BASE = ???   -- from: arm-none-eabi-nm pokefirered.elf | grep gOpenClawIPC
-- When request_ready == 1:
--   read npc_orig_text from IPC_BASE + 8
--   read map_num, map_group from IPC_BASE + 2,3
--   write to ipc/trigger.json
--   Python calls Gemini → encode response to Gen3
--   Python writes encoded bytes to IPC_BASE + 8 + 256  (ai_text offset)
--   Python writes 1 to IPC_BASE + 1                    (response_ready)
```

---

## Build Instructions

```bash
cd pokefirered/

# Install: arm-none-eabi-gcc, devkitARM
# See: INSTALL.md

# Build:
make -j4

# Output: pokefirered.gba, pokefirered.elf, pokefirered.map
```

---

## IPC Buffer Address — How to Find

```bash
arm-none-eabi-nm pokefirered.elf | grep gOpenClawIPC
arm-none-eabi-nm pokefirered.elf | grep gStringVar4
```

EWRAM addresses are in range `0x02000000–0x0203FFFF`.
Hardcode found address in bridge.lua.

---

## Key Differences: pokefirered vs pokeemerald

| Aspect | pokeemerald | pokefirered |
|--------|-------------|-------------|
| AddTextPrinter call | `AddTextPrinterForMessage(TRUE)` | `AddTextPrinterDiffStyle(TRUE)` |
| CHAR_SPACE | 0xAB (unverified in docs) | **0x00** (confirmed) |
| gStringVar4 | 0x3E8 bytes (1000) | 1000 bytes (same) |
| Task priority for dialog | 0x50 | 80 (same) |
| Direct gStringVar4 print | not documented | `ShowFieldMessageStringVar4()` in field_specials.c:120 |
| ScrCmd_message line | scrcmd.c:1265 | scrcmd.c:1263 |
| ShowFieldMessage line | field_message_box.c:62 | field_message_box.c:65 |

---

## Next Steps

1. Set up build environment (check INSTALL.md)
2. Run `make` — verify clean build of `pokefirered.gba`
3. Find `gStringVar4` address from ELF: `arm-none-eabi-nm pokefirered.elf | grep gStringVar4`
4. Add `src/openclaw_ai.c` + `include/openclaw_ai.h`
5. Hook `ShowFieldMessage()` in `field_message_box.c`
6. Rebuild → load in mGBA → verify dialog still works
7. Update `bridge.lua` to poll IPC buffer
8. Test end-to-end: NPC dialog → Lua detects → Python calls Gemini → AI text shown
