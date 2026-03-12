# OpenClaw — Claude Code Rules

## Core Behavior Rules

- **Never take action before a clear instruction.** If the request is ambiguous, ask first.
- **Read before edit.** Always read a file before modifying it. Never assume its contents.
- **Verify before assume.** If unsure about a function, struct, or behavior — search the codebase first.
- **Do one thing at a time.** Don't bundle unrequested changes, refactors, or "improvements" into a task.
- **No file creation unless explicitly asked.** Prefer editing existing files.
- **No commits or pushes unless explicitly asked.**
- **No adding comments, docstrings, or extra error handling** unless asked.

## Asking vs. Proceeding

| Situation | Action |
|-----------|--------|
| Clear instruction + known file | Proceed |
| Ambiguous scope or requirement | Ask first |
| Multiple valid approaches | Present options, ask which |
| Destructive or irreversible action | Always confirm first |
| Unsure about a GBA/C behavior | Say so, don't guess |

## Project Context

- Repo path: `GBA_AI_Project/`
- Target ROM: `pokefirered/` (decompiled Pokemon FireRed source)
- Build: `make -j4` inside `pokefirered/` → outputs `pokefirered.gba` + `.elf`
- Toolchain: `arm-none-eabi-gcc` (devkitARM)
- Key approach doc: `GBA_AI_Project/POKEFIRERED_APPROACH.md`
- Current task list: `GBA_AI_Project/work.md`

## GBA / C Specifics

- `EWRAM_DATA` variables live in `0x02000000–0x0203FFFF`
- `IWRAM_DATA` / `EWRAM_DATA` macros must be used for correct section placement
- Never guess EWRAM addresses — always derive from `.elf` using `arm-none-eabi-nm`
- Gen3 character encoding is in `pokefirered/include/characters.h` — check it, don't guess values

## Build & Test

- Build: `cd GBA_AI_Project/pokefirered && make -j4`
- Find symbol address: `arm-none-eabi-nm pokefirered.elf | grep <symbol>`
- Load ROM in mGBA manually — no automated emulator launch

## Git

- Commit only when explicitly asked
- Push only when explicitly asked
- Never use `--no-verify` or `--force` unless explicitly asked
- User: joey7614 / linjinglinjing123@gmail.com (already configured globally)
