-- Step 3o: BG1 tilemap read + write injection test
--
-- THEORY (from previous steps):
--   Dialog text uses pre-loaded font tiles (512+) in CharBlock1.
--   Text display = BG1 tilemap entries that point to specific font tile indices.
--   Injection = write different tile indices into the BG1 tilemap.
--
-- THIS SCRIPT:
--   1. On dialog open: read BG1 tilemap rows 8-13, log all non-zero positions
--   2. SWAP TEST: swap tile indices at positions 1 and 2
--      -> If the first two visible characters swap on screen = INJECTION CONFIRMED

local GTASKS_BASE = 0x03005E00
local TASK_SIZE   = 40
local TASK_COUNT  = 16
local DIALOG_FUNC = 0x08098155

local BG1_SCREEN  = 0x0600E800   -- screen_block=29
local TM_COLS     = 32

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

console:log("[Step3o] Ready. Talk to NPC.")
console:log("[Step3o] Will read BG1 tilemap rows 8-13 and swap first two character tiles.")

callbacks:add("frame", function()
    local open = is_dialog_open()

    if open and not done then
        done = true

        console:log("[Step3o] === Dialog open. BG1 tilemap rows 8-13: ===")
        local positions = {}

        for row = 8, 13 do
            local s   = string.format("  row%2d:", row)
            local has = false
            for col = 0, TM_COLS - 1 do
                local tm_idx = row * TM_COLS + col
                local addr   = BG1_SCREEN + tm_idx * 2
                local entry  = emu:read16(addr)
                local tile   = entry % 1024       -- bits[9:0] = tile index
                if tile ~= 0 then
                    s   = s .. string.format(" [c%d=t%d]", col, tile)
                    has = true
                    table.insert(positions, {
                        row   = row,
                        col   = col,
                        tile  = tile,
                        addr  = addr,
                        entry = entry,
                    })
                end
            end
            if has then console:log(s) end
        end

        console:log(string.format("[Step3o] %d non-zero tile positions found.", #positions))

        -- SWAP TEST: exchange tile indices of first two positions
        -- We preserve the palette/flag bits (entry & ~0x03FF) and replace tile index bits
        if #positions >= 2 then
            local p1 = positions[1]
            local p2 = positions[2]

            -- Build new entries: same palette flags, swapped tile index
            local flags1 = p1.entry - p1.tile  -- bits[15:10] preserved
            local flags2 = p2.entry - p2.tile
            local new1   = flags1 + p2.tile     -- p1 addr now shows p2's character
            local new2   = flags2 + p1.tile     -- p2 addr now shows p1's character

            emu:write16(p1.addr, new1)
            emu:write16(p2.addr, new2)

            console:log(string.format(
                "[Step3o] SWAP: [r%d,c%d]=t%d  <->  [r%d,c%d]=t%d",
                p1.row, p1.col, p1.tile,
                p2.row, p2.col, p2.tile))
            console:log("[Step3o] >>> Check dialog box NOW.")
            console:log("[Step3o]     If the first two visible characters swapped = INJECTION WORKS!")
            console:log("[Step3o]     If no change = game redraws tilemap every frame (need different approach).")

        elseif #positions == 1 then
            -- Only one tile? Replace it with tile+1 (adjacent font tile)
            local p = positions[1]
            local flags = p.entry - p.tile
            emu:write16(p.addr, flags + p.tile + 1)
            console:log(string.format(
                "[Step3o] SINGLE TILE: [r%d,c%d] t%d -> t%d",
                p.row, p.col, p.tile, p.tile + 1))
            console:log("[Step3o] >>> Did that character change?")

        else
            console:log("[Step3o] No text tiles found in rows 8-13.")
            console:log("[Step3o] Text may be in rows 14-19. Extending scan...")

            -- Scan wider range
            for row = 14, 20 do
                local s   = string.format("  row%2d:", row)
                local has = false
                for col = 0, TM_COLS - 1 do
                    local tm_idx = row * TM_COLS + col
                    local entry  = emu:read16(BG1_SCREEN + tm_idx * 2)
                    local tile   = entry % 1024
                    if tile ~= 0 then
                        s   = s .. string.format(" [c%d=t%d]", col, tile)
                        has = true
                    end
                end
                if has then console:log(s) end
            end
        end
    end

    if not open and done then
        done = false
        console:log("[Step3o] Dialog closed, reset.")
    end
end)
