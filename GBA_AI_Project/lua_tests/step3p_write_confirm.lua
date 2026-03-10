-- Step 3p: Confirm tilemap write works by swapping two DIFFERENT visible characters
--
-- step3o found:
--   row 8, col 7 = t516  (first char of line 1, e.g. 'I')
--   row 9, col 7 = t532  (first char of line 2, e.g. 'y')
--   → These are DIFFERENT tiles, swapping them must produce a visible change.
--
-- If the swap appears on screen = tilemap write injection CONFIRMED.
-- If no change = game redraws tilemap every frame (need alternate method).

local GTASKS_BASE = 0x03005E00
local TASK_SIZE   = 40
local TASK_COUNT  = 16
local DIALOG_FUNC = 0x08098155

local BG1_SCREEN = 0x0600E800
local TM_COLS    = 32

local done = false

local function is_dialog_open()
    for i = 0, TASK_COUNT - 1 do
        local slot = GTASKS_BASE + i * TASK_SIZE
        if emu:read8(slot + 4) == 1 and emu:read32(slot) == DIALOG_FUNC then
            return true
        end
    end
    return false
end

-- Find the first two text positions that have DIFFERENT tile indices
local function find_swap_pair()
    local all = {}
    for row = 8, 14 do
        for col = 0, TM_COLS - 1 do
            local addr  = BG1_SCREEN + (row * TM_COLS + col) * 2
            local entry = emu:read16(addr)
            local tile  = entry % 1024
            if tile ~= 0 then
                table.insert(all, {row=row, col=col, tile=tile, addr=addr, entry=entry})
            end
        end
    end

    -- Find first pair where tile indices differ
    for i = 1, #all do
        for j = i + 1, #all do
            if all[i].tile ~= all[j].tile then
                return all[i], all[j], #all
            end
        end
    end
    return nil, nil, #all
end

console:log("[Step3p] Ready. Talk to NPC.")
console:log("[Step3p] Will find first two DIFFERENT tiles in rows 8-14 and swap them.")

callbacks:add("frame", function()
    local open = is_dialog_open()

    if open and not done then
        done = true

        local p1, p2, total = find_swap_pair()

        console:log(string.format("[Step3p] Total non-zero tiles in rows 8-14: %d", total))

        if p1 then
            console:log(string.format(
                "[Step3p] Swap pair: [r%d,c%d]=t%d  <->  [r%d,c%d]=t%d",
                p1.row, p1.col, p1.tile,
                p2.row, p2.col, p2.tile))

            -- Swap the two entries (full 16-bit entry, preserving palette bits)
            emu:write16(p1.addr, p2.entry)
            emu:write16(p2.addr, p1.entry)

            console:log("[Step3p] Written!")
            console:log(string.format(
                "[Step3p] [r%d,c%d] now has t%d (was t%d)",
                p1.row, p1.col, p2.tile, p1.tile))
            console:log(string.format(
                "[Step3p] [r%d,c%d] now has t%d (was t%d)",
                p2.row, p2.col, p1.tile, p2.tile))
            console:log("[Step3p] >>> Did those two characters swap on screen?")
            console:log("[Step3p]     YES = tilemap write CONFIRMED! Next: build font map.")
            console:log("[Step3p]     NO  = game redraws tilemap each frame.")

            -- Also dump the full text area for context
            console:log("\n[Step3p] Full text area (rows 8-14, non-zero tiles):")
            for row = 8, 14 do
                local s = string.format("  row%2d:", row)
                local has = false
                for col = 0, TM_COLS - 1 do
                    local entry = emu:read16(BG1_SCREEN + (row * TM_COLS + col) * 2)
                    local tile  = entry % 1024
                    if tile ~= 0 then
                        s = s .. string.format("[c%d=t%d]", col, tile)
                        has = true
                    end
                end
                if has then console:log(s) end
            end
        else
            console:log("[Step3p] Could not find two tiles with different indices.")
            console:log("[Step3p] All visible tiles may be the same. Raw dump:")
            for row = 8, 14 do
                local s = string.format("  row%2d:", row)
                for col = 0, TM_COLS - 1 do
                    local entry = emu:read16(BG1_SCREEN + (row * TM_COLS + col) * 2)
                    local tile  = entry % 1024
                    if tile ~= 0 then
                        s = s .. string.format("[c%d=t%d]", col, tile)
                    end
                end
                console:log(s)
            end
        end
    end

    if not open and done then
        done = false
        console:log("[Step3p] Dialog closed, reset.")
    end
end)
