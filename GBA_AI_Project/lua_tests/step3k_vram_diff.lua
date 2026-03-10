-- Step 3k: CharBlock1 VRAM diff + BG scroll registers
--
-- FINDING from step3j:
--   - BG1 rows 8-13 = dialog box (displayed at screen bottom via scroll offset)
--   - Dialog text tiles are in CharBlock1: tile512-700 range (VRAM 0x06004000+)
--   - tile516@0x06004080 and tile532@0x06004280 have character pixel data
--   - VRAM is writable!
--
-- THIS SCRIPT:
--   1. Reads BG scroll registers (BG1VOFS confirms the ~48px scroll offset theory)
--   2. Snapshots CharBlock1 (0x06004000-0x06008000 = 16KB) BEFORE dialog
--   3. When dialog opens: diffs CharBlock1, reports changed 32-byte tile units
--   4. VISUAL TEST: zero-out tile516 to confirm it's a text character tile
--      (the character should visually disappear from dialog box)

local GTASKS_BASE = 0x03005E00
local TASK_SIZE   = 40
local TASK_COUNT  = 16
local DIALOG_FUNC = 0x08098155

-- BG scroll registers
local BG0HOFS = 0x04000010
local BG0VOFS = 0x04000012
local BG1HOFS = 0x04000014
local BG1VOFS = 0x04000016
local BG2HOFS = 0x04000018
local BG2VOFS = 0x0400001A

-- CharBlock1 (where dialog text tiles live based on tile indices 512-700+)
local CB1_START = 0x06004000
local CB1_SIZE  = 0x4000  -- 16KB
local TILE_SIZE = 32      -- bytes per 4bpp 8x8 tile

local snapshot    = {}
local snapshotted = false
local diffed      = false
local frame       = 0

local function is_dialog_open()
    for i = 0, TASK_COUNT - 1 do
        local slot = GTASKS_BASE + i * TASK_SIZE
        if emu:read8(slot + 4) == 1 and emu:read32(slot) == DIALOG_FUNC then
            return true
        end
    end
    return false
end

-- Read CharBlock1 as array of u32 words (faster than byte-by-byte)
local function snapshot_cb1()
    local s = {}
    for i = 0, CB1_SIZE/4 - 1 do
        s[i] = emu:read32(CB1_START + i * 4)
    end
    return s
end

console:log("[Step3k] Loaded. Waiting for dialog to be CLOSED first...")
console:log("[Step3k] Will snapshot VRAM before dialog, then diff when dialog opens.")

callbacks:add("frame", function()
    frame = frame + 1

    local open = is_dialog_open()

    -- Step 1: Take snapshot while dialog is CLOSED (first 10 frames)
    if not open and not snapshotted and frame == 10 then
        snapshot = snapshot_cb1()
        snapshotted = true
        console:log("[Step3k] CharBlock1 snapshot taken (no dialog).")

        -- Read BG scroll registers
        console:log(string.format("[Step3k] BG scroll: BG0 H=%d V=%d | BG1 H=%d V=%d | BG2 H=%d V=%d",
            emu:read16(BG0HOFS), emu:read16(BG0VOFS),
            emu:read16(BG1HOFS), emu:read16(BG1VOFS),
            emu:read16(BG2HOFS), emu:read16(BG2VOFS)))
        console:log("[Step3k] Now talk to NPC and HOLD dialog open.")
    end

    -- Step 2: Diff CharBlock1 when dialog opens
    if open and snapshotted and not diffed then
        diffed = true

        -- Read BG scroll during dialog
        console:log(string.format("[Step3k] BG scroll DURING dialog: BG1 H=%d V=%d",
            emu:read16(BG1HOFS), emu:read16(BG1VOFS)))

        local changed_tiles = {}
        for i = 0, CB1_SIZE/4 - 1 do
            local cur = emu:read32(CB1_START + i * 4)
            if cur ~= snapshot[i] then
                local tile_num = math.floor(i * 4 / TILE_SIZE)
                local tile_addr = CB1_START + tile_num * TILE_SIZE
                local abs_tile = 512 + tile_num  -- char block 0 has 512 tiles before this
                if not changed_tiles[tile_num] then
                    changed_tiles[tile_num] = {addr=tile_addr, idx=abs_tile}
                end
            end
        end

        -- Sort and print changed tiles
        local sorted = {}
        for tile_num, info in pairs(changed_tiles) do
            table.insert(sorted, {tile_num=tile_num, info=info})
        end
        table.sort(sorted, function(a,b) return a.tile_num < b.tile_num end)

        console:log(string.format("[Step3k] CharBlock1 changed tiles: %d", #sorted))
        console:log("[Step3k] Changed tiles (these are NEW dialog content):")

        for _, entry in ipairs(sorted) do
            local addr = entry.info.addr
            local idx  = entry.info.idx
            -- Dump first 16 bytes of this tile
            local bytes = ""
            for b = 0, 15 do
                bytes = bytes .. string.format("%02X ", emu:read8(addr + b))
            end
            console:log(string.format("  tile%d @ 0x%08X: %s", idx, addr, bytes))
        end

        -- VISUAL TEST: Zero out the FIRST changed text tile to see if char disappears
        if #sorted > 0 then
            local test_tile = sorted[1].info
            console:log(string.format("\n[Step3k] VISUAL TEST: zeroing tile%d @ 0x%08X",
                test_tile.idx, test_tile.addr))
            console:log("[Step3k] Watch dialog box - one character should disappear!")
            for b = 0, TILE_SIZE - 1 do
                emu:write8(test_tile.addr + b, 0x00)
            end
        end
    end

    if not open and diffed then
        diffed = false
        snapshotted = false
        console:log("[Step3k] Dialog closed. Will re-snapshot on next open.")
    end
end)
