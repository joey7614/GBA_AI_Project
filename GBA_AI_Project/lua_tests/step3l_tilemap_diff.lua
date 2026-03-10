-- Step 3l: BG1 TILEMAP diff (not char data) + visual zero test
--
-- KEY INSIGHT: CharBlock1 = 0 changes because font tiles are PRE-LOADED.
--   Text rendering = changing TILEMAP entries to point to different font tiles.
--   We need to diff the TILEMAP at 0x0600E800 (BG1 screen block 29).
--
-- Also does a visual test: zero out the char data for tile indices seen
-- in BG1 rows 8-13 during dialog, to confirm which are text characters.
--
-- BG1 screen block 29 @ 0x0600E800
--   32 rows × 32 cols × 2 bytes = 2048 bytes total
--   Each entry: bits[9:0] = tile_index, bit[10]=h_flip, bit[11]=v_flip,
--               bits[15:12] = palette bank

local GTASKS_BASE  = 0x03005E00
local TASK_SIZE    = 40
local TASK_COUNT   = 16
local DIALOG_FUNC  = 0x08098155

local BG1_SCREEN   = 0x0600E800   -- BG1 tilemap (screen_block=29)
local BG1_CHAR     = 0x06000000   -- BG1 char data (char_block=0)
local TILE_BYTES   = 32           -- 4bpp 8x8 = 32 bytes per tile
local TM_ROWS      = 32
local TM_COLS      = 32

local snapshot_tm  = {}
local snapshotted  = false
local diffed       = false
local frame        = 0

local function is_dialog_open()
    for i = 0, TASK_COUNT - 1 do
        local slot = GTASKS_BASE + i * TASK_SIZE
        if emu:read8(slot + 4) == 1 and emu:read32(slot) == DIALOG_FUNC then
            return true
        end
    end
    return false
end

local function read_tilemap()
    local tm = {}
    for i = 0, TM_ROWS * TM_COLS - 1 do
        tm[i] = emu:read16(BG1_SCREEN + i * 2)
    end
    return tm
end

local function tm_idx(row, col)
    return row * TM_COLS + col
end

-- Zero out a tile's char data
local function zero_tile(tile_idx)
    local addr = BG1_CHAR + tile_idx * TILE_BYTES
    for b = 0, TILE_BYTES - 1 do
        emu:write8(addr + b, 0x00)
    end
end

console:log("[Step3l] Loaded. Will snapshot BG1 TILEMAP (not char data) before dialog.")
console:log("[Step3l] Wait for 'snapshot taken', THEN talk to NPC and hold dialog open.")

callbacks:add("frame", function()
    frame = frame + 1
    local open = is_dialog_open()

    -- Snapshot while dialog is closed
    if not open and not snapshotted and frame == 15 then
        snapshot_tm = read_tilemap()
        snapshotted = true
        console:log("[Step3l] BG1 tilemap snapshot taken. Now talk to NPC!")
    end

    -- Diff tilemap when dialog opens
    if open and snapshotted and not diffed then
        diffed = true
        local curr_tm = read_tilemap()

        -- Find changed tilemap entries
        local changes = {}
        for row = 0, TM_ROWS - 1 do
            for col = 0, TM_COLS - 1 do
                local i = tm_idx(row, col)
                if curr_tm[i] ~= snapshot_tm[i] then
                    local old_tile = snapshot_tm[i] % 1024
                    local new_tile = curr_tm[i] % 1024
                    table.insert(changes, {
                        row=row, col=col,
                        old=old_tile, new=new_tile,
                        entry=curr_tm[i]
                    })
                end
            end
        end

        console:log(string.format("[Step3l] BG1 tilemap changes: %d entries", #changes))

        if #changes > 0 then
            console:log("[Step3l] Changed tilemap entries (row, col, old_tile -> new_tile):")
            for _, c in ipairs(changes) do
                console:log(string.format("  BG1[%2d][%2d]: tile%d -> tile%d",
                    c.row, c.col, c.old, c.new))
            end

            -- Identify unique new tile indices (the text characters)
            local unique_new = {}
            for _, c in ipairs(changes) do
                if c.new ~= 0 then
                    unique_new[c.new] = true
                end
            end
            console:log("[Step3l] Unique NEW tile indices used for dialog text:")
            local sorted_tiles = {}
            for tile_idx, _ in pairs(unique_new) do
                table.insert(sorted_tiles, tile_idx)
            end
            table.sort(sorted_tiles)
            for _, t in ipairs(sorted_tiles) do
                local addr = BG1_CHAR + t * TILE_BYTES
                local b0 = string.format("%02X %02X %02X %02X",
                    emu:read8(addr), emu:read8(addr+1),
                    emu:read8(addr+2), emu:read8(addr+3))
                console:log(string.format("  tile%d @ 0x%08X: %s ...", t, addr, b0))
            end
        else
            -- Tilemap also unchanged - do visual test on known tiles from step3j
            console:log("[Step3l] Tilemap unchanged too! Trying direct visual test...")
            console:log("[Step3l] Zeroing tile516 and tile532 to see if chars disappear:")
            console:log("  tile516 @ 0x06004080 -> ZEROED")
            console:log("  tile532 @ 0x06004280 -> ZEROED")
            zero_tile(516)
            zero_tile(532)
            console:log("[Step3l] Did any characters disappear from dialog box?")
        end
    end

    if not open and diffed then
        diffed = false
        snapshotted = false
        console:log("[Step3l] Dialog closed. Restart mGBA or reload save to reset VRAM, then try again.")
    end
end)
