-- Step 3j: Full VRAM scan - find WHERE the dialog text tiles actually are
--
-- CONFIRMED: VRAM writable, text renders before gTasks detection.
-- PROBLEM:   step3i showed only 2 non-zero tiles in dialog area (BG1 row17).
--            WIN0+WIN1 enabled (DISPCNT bit 13-14) - windows may clip BG layers.
--            BG2 and BG3 were not checked.
--
-- This script:
--   1. Reads Window registers (WIN0H/V, WIN1H/V, WININ, WINOUT)
--   2. Scans ALL 32 rows of ALL 4 BGs for non-zero tile entries
--   3. For tiles found, dumps char pixel data (to verify text is there)
--   4. Scans 4KB of OBJ char data (0x06010000) to check if text uses sprites

local GTASKS_BASE = 0x03005E00
local TASK_SIZE   = 40
local TASK_COUNT  = 16
local DIALOG_FUNC = 0x08098155

-- I/O registers
local DISPCNT = 0x04000000
local BG0CNT  = 0x04000008
local BG1CNT  = 0x0400000A
local BG2CNT  = 0x0400000C
local BG3CNT  = 0x0400000E
local WIN0H   = 0x04000040
local WIN0V   = 0x04000044
local WIN1H   = 0x04000042
local WIN1V   = 0x04000046
local WININ   = 0x04000048
local WINOUT  = 0x0400004A

local VRAM_BASE = 0x06000000

local printed = false

local function is_dialog_open()
    for i = 0, TASK_COUNT - 1 do
        local slot = GTASKS_BASE + i * TASK_SIZE
        if emu:read8(slot + 4) == 1 and emu:read32(slot) == DIALOG_FUNC then
            return true
        end
    end
    return false
end

local function scan_bg(bg_name, screen_addr, char_addr, is_4bpp)
    local bytes_per_tile = is_4bpp == 1 and 32 or 64
    local nonzero_rows = {}
    for row = 0, 31 do
        local row_tiles = {}
        local has_nonzero = false
        for col = 0, 31 do
            local entry = emu:read16(screen_addr + (row * 32 + col) * 2)
            local tile_idx = entry % 1024
            if tile_idx ~= 0 then
                has_nonzero = true
                table.insert(row_tiles, {col=col, tile=tile_idx})
            end
        end
        if has_nonzero then
            table.insert(nonzero_rows, {row=row, tiles=row_tiles})
        end
    end

    if #nonzero_rows == 0 then
        console:log(string.format("  %s: all zeros (blank)", bg_name))
        return
    end

    for _, r in ipairs(nonzero_rows) do
        local s = string.format("  %s row%2d:", bg_name, r.row)
        for _, t in ipairs(r.tiles) do
            s = s .. string.format(" [col%2d=tile%d]", t.col, t.tile)
        end
        console:log(s)

        -- Dump char data for first 3 unique tiles in this row
        local dumped = {}
        local count = 0
        for _, t in ipairs(r.tiles) do
            if not dumped[t.tile] and count < 3 then
                dumped[t.tile] = true
                count = count + 1
                local tile_addr = char_addr + t.tile * bytes_per_tile
                local bytes = ""
                for b = 0, math.min(15, bytes_per_tile-1) do
                    bytes = bytes .. string.format("%02X ", emu:read8(tile_addr + b))
                end
                console:log(string.format("    tile%d @ 0x%08X: %s", t.tile, tile_addr, bytes))
            end
        end
    end
end

local function count_nonzero_vram(start, len, label)
    local count = 0
    for i = 0, len - 1, 4 do
        local v = emu:read32(start + i)
        if v ~= 0 then count = count + 1 end
    end
    console:log(string.format("  %s [0x%08X, +0x%X]: %d/%d u32 words non-zero",
        label, start, len, count, len/4))
end

console:log("[Step3j] Loaded. Talk to NPC and HOLD dialog open.")

callbacks:add("frame", function()
    local open = is_dialog_open()

    if open and not printed then
        printed = true

        -- Window registers
        local w0h = emu:read16(WIN0H)
        local w0v = emu:read16(WIN0V)
        local w1h = emu:read16(WIN1H)
        local w1v = emu:read16(WIN1V)
        local winin  = emu:read16(WININ)
        local winout = emu:read16(WINOUT)
        console:log("[Step3j] Window config:")
        console:log(string.format("  WIN0: H=x%d-%d  V=y%d-%d",
            math.floor(w0h/256), w0h%256, math.floor(w0v/256), w0v%256))
        console:log(string.format("  WIN1: H=x%d-%d  V=y%d-%d",
            math.floor(w1h/256), w1h%256, math.floor(w1v/256), w1v%256))
        console:log(string.format("  WININ=0x%04X (inside WIN0: BG%d%d%d%d+OBJ=%d) WINOUT=0x%04X",
            winin,
            winin%2, math.floor(winin/2)%2, math.floor(winin/4)%2, math.floor(winin/8)%2,
            math.floor(winin/16)%2,
            winout))

        -- Read BG settings
        local bg_info = {
            {name="BG0", cnt=emu:read16(BG0CNT)},
            {name="BG1", cnt=emu:read16(BG1CNT)},
            {name="BG2", cnt=emu:read16(BG2CNT)},
            {name="BG3", cnt=emu:read16(BG3CNT)},
        }

        console:log("\n[Step3j] Scanning all BG tilemaps for non-zero tiles:")
        for _, bg in ipairs(bg_info) do
            local char_base   = math.floor(bg.cnt / 4) % 4
            local screen_base = math.floor(bg.cnt / 256) % 32
            local is_4bpp     = math.floor(bg.cnt / 128) % 2 == 0 and 1 or 0
            local char_addr   = VRAM_BASE + char_base * 0x4000
            local screen_addr = VRAM_BASE + screen_base * 0x800
            scan_bg(bg.name, screen_addr, char_addr, is_4bpp)
        end

        -- Check VRAM char data density (non-zero = has tile pixels)
        console:log("\n[Step3j] VRAM char data density (non-zero words):")
        count_nonzero_vram(0x06000000, 0x4000, "CharBlock0 (BG1/2/3)")
        count_nonzero_vram(0x06004000, 0x4000, "CharBlock1")
        count_nonzero_vram(0x06008000, 0x4000, "CharBlock2 (BG0)")
        count_nonzero_vram(0x0600C000, 0x4000, "CharBlock3")
        count_nonzero_vram(0x06010000, 0x4000, "OBJ CharBlock0")
        count_nonzero_vram(0x06014000, 0x4000, "OBJ CharBlock1")
    end

    if not open and printed then
        printed = false
        console:log("[Step3j] Dialog closed.")
    end
end)
