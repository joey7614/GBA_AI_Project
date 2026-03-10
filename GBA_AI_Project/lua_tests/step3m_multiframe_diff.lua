-- Step 3m: Multi-frame tilemap diff (BG1 + BG2) for first 30 frames of dialog
--
-- INSIGHT from step3l: At dialog open frame 1, BG1 tiles get CLEARED (→tile0).
--   The actual text tiles are written in SUBSEQUENT frames by the TextPrinter.
--   We need to track tilemap changes across frames 1-30 to catch when text appears.
--
-- Also monitors BG2 tilemap (screen_block=28 @ 0x0600E000) which may hold
-- the dialog frame/box tiles.
--
-- OUTPUT: Shows exactly which frame, which BG, which row/col the text tiles appear.
--         This tells us WHERE and WHEN to inject our AI text.

local GTASKS_BASE = 0x03005E00
local TASK_SIZE   = 40
local TASK_COUNT  = 16
local DIALOG_FUNC = 0x08098155

local BG1_SCREEN  = 0x0600E800   -- BG1 tilemap (screen_block=29)
local BG2_SCREEN  = 0x0600E000   -- BG2 tilemap (screen_block=28)
local TM_SIZE     = 32 * 32      -- 1024 entries per tilemap
local BG1_CHAR    = 0x06000000   -- BG1 char data base

local frame       = 0
local in_dialog   = false
local d_frame     = 0
local MAX_TRACK   = 60           -- track first 60 dialog frames

local prev_bg1    = {}
local prev_bg2    = {}

local function is_dialog_open()
    for i = 0, TASK_COUNT - 1 do
        local slot = GTASKS_BASE + i * TASK_SIZE
        if emu:read8(slot + 4) == 1 and emu:read32(slot) == DIALOG_FUNC then
            return true
        end
    end
    return false
end

local function read_tilemap(base)
    local tm = {}
    for i = 0, TM_SIZE - 1 do
        tm[i] = emu:read16(base + i * 2)
    end
    return tm
end

local function diff_tilemaps(old_tm, new_tm, bg_name, d_frame_num)
    local changes = {}
    for i = 0, TM_SIZE - 1 do
        if new_tm[i] ~= old_tm[i] then
            local row = math.floor(i / 32)
            local col = i % 32
            local old_tile = old_tm[i] % 1024
            local new_tile = new_tm[i] % 1024
            table.insert(changes, {row=row, col=col, old=old_tile, new=new_tile})
        end
    end
    if #changes > 0 then
        local s = string.format("  [f%2d] %s: %d changes", d_frame_num, bg_name, #changes)
        for _, c in ipairs(changes) do
            s = s .. string.format(" [r%d,c%d:%d->%d]", c.row, c.col, c.old, c.new)
        end
        console:log(s)

        -- For each NEW non-zero tile, show its char data (first 8 bytes)
        local shown = {}
        for _, c in ipairs(changes) do
            if c.new ~= 0 and not shown[c.new] then
                shown[c.new] = true
                local addr = BG1_CHAR + c.new * 32
                console:log(string.format("    NEW tile%d@0x%08X: %02X%02X %02X%02X %02X%02X %02X%02X",
                    c.new, addr,
                    emu:read8(addr), emu:read8(addr+1),
                    emu:read8(addr+2), emu:read8(addr+3),
                    emu:read8(addr+4), emu:read8(addr+5),
                    emu:read8(addr+6), emu:read8(addr+7)))
            end
        end
    end
    return #changes > 0
end

console:log("[Step3m] Multi-frame BG1+BG2 tilemap diff for first 60 dialog frames.")
console:log("[Step3m] Talk to NPC - output shows WHEN and WHERE text tiles appear.")

callbacks:add("frame", function()
    frame = frame + 1
    local open = is_dialog_open()

    if open and not in_dialog then
        in_dialog = true
        d_frame   = 0
        -- Initialize prev state to current state at dialog open
        prev_bg1  = read_tilemap(BG1_SCREEN)
        prev_bg2  = read_tilemap(BG2_SCREEN)
        console:log(string.format("\n[Step3m] === Dialog OPEN (frame %d) ===", frame))
        console:log("[Step3m] Tracking BG1+BG2 tilemap changes for 60 frames...")
    end

    if in_dialog and open then
        d_frame = d_frame + 1

        if d_frame <= MAX_TRACK then
            local curr_bg1 = read_tilemap(BG1_SCREEN)
            local curr_bg2 = read_tilemap(BG2_SCREEN)

            diff_tilemaps(prev_bg1, curr_bg1, "BG1", d_frame)
            diff_tilemaps(prev_bg2, curr_bg2, "BG2", d_frame)

            prev_bg1 = curr_bg1
            prev_bg2 = curr_bg2

            if d_frame == MAX_TRACK then
                console:log("[Step3m] 60-frame tracking done. Waiting for dialog close.")
            end
        end
    end

    if not open and in_dialog then
        in_dialog = false
        console:log(string.format("[Step3m] Dialog CLOSED after %d frames.", d_frame))
    end
end)
