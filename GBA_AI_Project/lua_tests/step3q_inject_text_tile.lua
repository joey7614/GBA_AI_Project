-- Step 3q: Write-verify + continuous inject on actual TEXT tiles
--
-- step3p confirmed: emu:write16(tilemap addr) DOES update VRAM memory.
-- But we swapped border tiles (t547/t548) that look identical visually.
--
-- This script specifically targets col 7 (the first text character position):
--   row 8, col 7 = first char of line 1  (e.g. t516 = letter 'I')
--   row 9, col 7 = first char of line 2  (e.g. t532 = letter 'y')
--
-- TWO-STEP TEST:
--   A) Tilemap test: write t532 (line 2 char) into row 8 col 7 (line 1 char)
--      -> If line 1's first char changes to match line 2's first char = tilemap injection works
--   B) Pixel data test: write all-0xFF into tile 516's char data (makes it solid color 15)
--      -> If a char turns into a solid block = pixel data injection works
--
-- Also: re-write every frame in case the game re-renders the tilemap.

local GTASKS_BASE = 0x03005E00
local TASK_SIZE   = 40
local TASK_COUNT  = 16
local DIALOG_FUNC = 0x08098155

local BG1_SCREEN = 0x0600E800
local TM_COLS    = 32
local CB1_BASE   = 0x06000000  -- char data base (BG1 char_block=0)
local TILE_BYTES = 32

local in_dialog  = false
local d_frame    = 0

-- Tilemap addresses for col 7 of rows 8 and 9
local ADDR_R8C7  = BG1_SCREEN + (8 * TM_COLS + 7) * 2
local ADDR_R9C7  = BG1_SCREEN + (9 * TM_COLS + 7) * 2

local orig_r8c7  = nil
local orig_r9c7  = nil

local function is_dialog_open()
    for i = 0, TASK_COUNT - 1 do
        local slot = GTASKS_BASE + i * TASK_SIZE
        if emu:read8(slot + 4) == 1 and emu:read32(slot) == DIALOG_FUNC then
            return true
        end
    end
    return false
end

console:log("[Step3q] Ready. Talk to NPC.")
console:log("[Step3q] Will swap line1/line2 first chars AND corrupt tile516 pixels.")

callbacks:add("frame", function()
    local open = is_dialog_open()

    if open and not in_dialog then
        in_dialog   = true
        d_frame     = 0
        orig_r8c7   = emu:read16(ADDR_R8C7)
        orig_r9c7   = emu:read16(ADDR_R9C7)
        local t1    = orig_r8c7 % 1024
        local t2    = orig_r9c7 % 1024
        console:log(string.format(
            "[Step3q] Dialog open! r8c7=t%d (0x%04X)  r9c7=t%d (0x%04X)",
            t1, orig_r8c7, t2, orig_r9c7))
    end

    if in_dialog and open then
        d_frame = d_frame + 1

        -- === TEST A: Tilemap swap (line1 char <-> line2 char) every frame ===
        -- Swap the tile indices; preserve palette flags
        local t1_cur = emu:read16(ADDR_R8C7)
        local t2_cur = emu:read16(ADDR_R9C7)
        emu:write16(ADDR_R8C7, orig_r9c7)   -- put line2 char at line1 position
        emu:write16(ADDR_R9C7, orig_r8c7)   -- put line1 char at line2 position

        -- === TEST B: Corrupt tile 516 pixel data with solid color 15 ===
        -- Only on frame 1; if it works, char 'I' becomes a solid block everywhere it's used
        if d_frame == 1 then
            local tile516_addr = CB1_BASE + 516 * TILE_BYTES  -- 0x06004080
            console:log(string.format(
                "[Step3q] f1: Writing 0xFF (solid palette-15) to tile516 @ 0x%08X",
                tile516_addr))
            for b = 0, TILE_BYTES - 1 do
                emu:write8(tile516_addr + b, 0xFF)
            end
            console:log("[Step3q] TEST A: r8c7 swapped with r9c7 every frame.")
            console:log("[Step3q] TEST B: tile516 pixel data set to solid 0xFF.")
            console:log("[Step3q] >>> Look at the dialog box:")
            console:log("[Step3q]     Test A: first char of line1 should = first char of line2")
            console:log("[Step3q]     Test B: any char using tile516 should become solid color")
        end

        -- Frame 2: check if tilemap write persisted (game redraw detection)
        if d_frame == 2 then
            local r8_now = emu:read16(ADDR_R8C7)
            if r8_now % 1024 == orig_r8c7 % 1024 then
                console:log("[Step3q] f2: r8c7 was RESTORED by game -> game redraws tilemap each frame!")
                console:log("[Step3q]     Continuous per-frame overwrite active (keep script running).")
            else
                console:log(string.format(
                    "[Step3q] f2: r8c7 = t%d (still our value) -> write persisted!", r8_now % 1024))
            end
        end

        if d_frame <= 5 or d_frame % 60 == 0 then
            console:log(string.format(
                "[Step3q] f%d: r8c7=0x%04X r9c7=0x%04X (writing swap each frame)",
                d_frame,
                emu:read16(ADDR_R8C7),
                emu:read16(ADDR_R9C7)))
        end
    end

    if not open and in_dialog then
        in_dialog = false
        console:log("[Step3q] Dialog closed.")
    end
end)
