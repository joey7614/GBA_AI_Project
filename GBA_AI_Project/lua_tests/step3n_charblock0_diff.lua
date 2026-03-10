-- Step 3n: CharBlock0 diff between two dialogs
--
-- KEY INSIGHT: BG1/BG2/BG3 all use char_block=0 (0x06000000).
--   Text character pixels are rendered into CharBlock0 tiles.
--   Tilemap entries are FIXED (set up synchronously before task creation).
--   Only pixel DATA changes between different dialog texts.
--
-- METHOD: Two-dialog approach (works with save states, no fresh start needed)
--   Dialog 1: snapshot CharBlock0 tile data
--   Dialog 2: diff CharBlock0 → changed tiles = text rendering buffer!
--   Also works by closing dialog, opening same NPC again (same text = no changes,
--   but different NPC/sign = changes reveal text tile locations).
--
-- CharBlock0 = 0x06000000 to 0x06003FFF (16KB = 512 tiles × 32 bytes each)

local GTASKS_BASE = 0x03005E00
local TASK_SIZE   = 40
local TASK_COUNT  = 16
local DIALOG_FUNC = 0x08098155

local CB0_BASE  = 0x06000000
local CB0_SIZE  = 0x4000        -- 16KB = CharBlock0
local TILE_SIZE = 32            -- 4bpp 8x8 tile

local snapshot   = {}
local dialog_num = 0
local in_dialog  = false
local frame      = 0

local function is_dialog_open()
    for i = 0, TASK_COUNT - 1 do
        local slot = GTASKS_BASE + i * TASK_SIZE
        if emu:read8(slot + 4) == 1 and emu:read32(slot) == DIALOG_FUNC then
            return true
        end
    end
    return false
end

local function snapshot_cb0()
    local s = {}
    for i = 0, CB0_SIZE / 4 - 1 do
        s[i] = emu:read32(CB0_BASE + i * 4)
    end
    return s
end

console:log("[Step3n] Two-dialog CharBlock0 diff.")
console:log("[Step3n] Step 1: Talk to NPC #1 (snapshot taken when dialog opens).")
console:log("[Step3n] Step 2: Close dialog.")
console:log("[Step3n] Step 3: Talk to DIFFERENT NPC/sign (diff shown).")
console:log("[Step3n] Changed tiles = text rendering buffer = injection targets!")

callbacks:add("frame", function()
    frame = frame + 1
    local open = is_dialog_open()

    if open and not in_dialog then
        in_dialog  = true
        dialog_num = dialog_num + 1

        if dialog_num == 1 then
            -- First dialog: take snapshot
            snapshot = snapshot_cb0()
            console:log(string.format("[Step3n] Dialog #1 open! CharBlock0 snapshot taken."))
            console:log("[Step3n] Close dialog, then talk to a DIFFERENT NPC or sign.")

        elseif dialog_num >= 2 then
            -- Second+ dialog: diff vs snapshot
            local curr = snapshot_cb0()
            local changed_tiles = {}
            local text_tiles    = {}

            for i = 0, CB0_SIZE / 4 - 1 do
                if curr[i] ~= snapshot[i] then
                    local tile_num = math.floor(i * 4 / TILE_SIZE)
                    changed_tiles[tile_num] = true
                end
            end

            -- Sort changed tiles and classify
            local sorted = {}
            for t, _ in pairs(changed_tiles) do
                table.insert(sorted, t)
            end
            table.sort(sorted)

            console:log(string.format("\n[Step3n] Dialog #%d: CharBlock0 changed tiles: %d",
                dialog_num, #sorted))

            if #sorted > 0 then
                console:log("[Step3n] Changed tiles (tile index, VRAM addr, first 16 bytes):")
                for _, t in ipairs(sorted) do
                    local addr = CB0_BASE + t * TILE_SIZE
                    local bytes = ""
                    for b = 0, 15 do
                        bytes = bytes .. string.format("%02X ", emu:read8(addr + b))
                    end
                    -- Check if it looks like a text tile (some transparent pixels)
                    local has_zero = emu:read32(addr) == 0 or emu:read32(addr+4) == 0
                    local tag = has_zero and " <TEXT?>" or ""
                    console:log(string.format("  tile%3d @ 0x%08X: %s%s", t, addr, bytes, tag))
                end

                -- Update snapshot with latest for next dialog
                snapshot = curr
                console:log("[Step3n] Snapshot updated. Talk to another NPC to continue diffing.")
            else
                console:log("[Step3n] No changes in CharBlock0 either!")
                console:log("[Step3n] Text may be in CharBlock2/3 or OBJ tiles.")
                console:log("[Step3n] Next: check BG2 tilemap for text rows.")
                snapshot = curr
            end
        end
    end

    if not open and in_dialog then
        in_dialog = false
    end
end)
