-- OpenClaw Bridge v2.0
-- Runs inside mGBA (Tools -> Scripting -> Load Script)
-- Communicates with Python via files in the ipc/ folder
--
-- FILE PROTOCOL:
--   ipc/trigger.json  -- Lua writes this when dialog opens
--   ipc/response.txt  -- Python writes AI text here
--   ipc/status.txt    -- Lua writes current state (for debugging)
--
-- ALL ADDRESSES CONFIRMED via step2d / step2j testing

-- ============================================================
-- CONFIG
-- ============================================================
local IPC_DIR       = "C:/Users/JL/Documents/GBA_AI_Project/ipc/"
local TRIGGER_FILE  = IPC_DIR .. "trigger.json"
local RESPONSE_FILE = IPC_DIR .. "response.txt"
local STATUS_FILE   = IPC_DIR .. "status.txt"

-- ---- Dialog detection via gTasks (CONFIRMED) ----
local GTASKS_BASE = 0x03005E00   -- confirmed via step2i scan
local TASK_SIZE   = 40
local TASK_COUNT  = 16
local DIALOG_FUNC = 0x08098155   -- confirmed via step2j (appeared on every dialog open)

-- ---- Player data via pointer chain (CONFIRMED) ----
local SAVEBLOCK2_PTR = 0x03005D90  -- fixed IWRAM ptr -> dynamic EWRAM addr
local SAVEBLOCK1_PTR = 0x03005D8C  -- fixed IWRAM ptr -> dynamic EWRAM addr

-- ---- Text buffer for response injection ----
local TEXT_BUFFER = 0x0202161C    -- gTextBuffer (from pokeemerald research)

-- ============================================================
-- CHARACTER ENCODING (Pokemon Gen 3)
-- ============================================================
local CHARSET = {}   -- byte -> ASCII (for reading)
local REVMAP  = {}   -- ASCII code -> byte (for writing)

for i = 0, 25 do
    CHARSET[0xBB + i] = string.char(65 + i)   -- A-Z
    CHARSET[0xD5 + i] = string.char(97 + i)   -- a-z
    REVMAP[65 + i] = 0xBB + i
    REVMAP[97 + i] = 0xD5 + i
end
CHARSET[0xAB] = " "
CHARSET[0xAD] = "!"
CHARSET[0xAE] = "?"
CHARSET[0xAF] = "."
REVMAP[32] = 0xAB
REVMAP[33] = 0xAD
REVMAP[63] = 0xAE
REVMAP[46] = 0xAF

local function decode_string(addr, max_len)
    local result = ""
    for i = 0, max_len - 1 do
        local b = emu:read8(addr + i)
        if b == 0xFF or b == 0x00 then break end
        result = result .. (CHARSET[b] or "")
    end
    return result
end

local function encode_string(text, addr, max_len)
    local written = 0
    for i = 1, #text do
        if written >= max_len - 1 then break end
        local c = string.byte(text, i)
        emu:write8(addr + written, REVMAP[c] or 0xAB)
        written = written + 1
    end
    emu:write8(addr + written, 0xFF)
    return written
end

-- ============================================================
-- GAME STATE READERS
-- ============================================================
local function is_dialog_open()
    for i = 0, TASK_COUNT - 1 do
        local slot = GTASKS_BASE + i * TASK_SIZE
        if emu:read8(slot + 4) == 1 then         -- active flag
            if emu:read32(slot) == DIALOG_FUNC then  -- func pointer
                return true
            end
        end
    end
    return false
end

local function get_player_name()
    local sb2 = emu:read32(SAVEBLOCK2_PTR)
    if sb2 == 0 then return "???" end
    return decode_string(sb2, 8)
end

local function get_map_info()
    local sb1 = emu:read32(SAVEBLOCK1_PTR)
    if sb1 == 0 then return 0, 0, 0, 0 end
    local x         = emu:read16(sb1 + 0x00)
    local y         = emu:read16(sb1 + 0x02)
    local map_group = emu:read8(sb1  + 0x04)
    local map_num   = emu:read8(sb1  + 0x05)
    return x, y, map_group, map_num
end

-- ============================================================
-- IPC HELPERS
-- ============================================================
local function file_exists(path)
    local f = io.open(path, "r")
    if f then f:close() return true end
    return false
end

local function write_file(path, content)
    local f = io.open(path, "w")
    if f then f:write(content) f:close() return true end
    return false
end

local function read_file(path)
    local f = io.open(path, "r")
    if f then local c = f:read("*a") f:close() return c end
    return nil
end

local function json_escape(s)
    s = s:gsub('\\', '\\\\')
    s = s:gsub('"', '\\"')
    return s
end

-- ============================================================
-- STATE
-- ============================================================
local last_dialog   = false
local waiting       = false
local wait_frames   = 0
local frame_count   = 0
local TIMEOUT_FRAMES = 600   -- 10 seconds

-- ============================================================
-- MAIN FRAME LOOP
-- ============================================================
callbacks:add("frame", function()
    frame_count = frame_count + 1

    local dialog = is_dialog_open()

    -- ---- New dialog detected ----
    if not last_dialog and dialog and not waiting then
        local player = get_player_name()
        local x, y, mg, mn = get_map_info()

        os.remove(TRIGGER_FILE)
        os.remove(RESPONSE_FILE)

        local trigger = string.format(
            '{"player_name":"%s","map_group":%d,"map_num":%d,"pos_x":%d,"pos_y":%d,"frame":%d}',
            json_escape(player), mg, mn, x, y, frame_count
        )

        if write_file(TRIGGER_FILE, trigger) then
            waiting    = true
            wait_frames = 0
            console:log(string.format(
                "[OpenClaw] Dialog! Player='%s' map=%d/%d pos=(%d,%d)",
                player, mg, mn, x, y))
        else
            console:log("[OpenClaw] ERROR: cannot write trigger file!")
        end
    end

    -- ---- Dialog closed before response arrived ----
    if last_dialog and not dialog and waiting then
        console:log("[OpenClaw] Dialog closed (no response yet). Cleaning up.")
        os.remove(TRIGGER_FILE)
        os.remove(RESPONSE_FILE)
        waiting = false
    end

    -- ---- Poll for AI response ----
    if waiting and dialog then
        wait_frames = wait_frames + 1

        if file_exists(RESPONSE_FILE) then
            local response = read_file(RESPONSE_FILE)
            os.remove(RESPONSE_FILE)
            os.remove(TRIGGER_FILE)

            if response and #response > 0 then
                response = response:match("^%s*(.-)%s*$")
                local written = encode_string(response, TEXT_BUFFER, 80)
                waiting = false
                console:log(string.format(
                    "[OpenClaw] Injected %d chars: '%s'", written, response:sub(1, 50)))
            end
        end

        if wait_frames >= TIMEOUT_FRAMES then
            console:log("[OpenClaw] TIMEOUT. Resetting.")
            os.remove(TRIGGER_FILE)
            waiting    = false
            wait_frames = 0
        end
    end

    -- ---- Status file (every 5 seconds) ----
    if frame_count % 300 == 0 then
        write_file(STATUS_FILE, string.format(
            '{"frame":%d,"dialog":%s,"waiting":%s}',
            frame_count,
            dialog and "true" or "false",
            waiting and "true" or "false"
        ))
    end

    last_dialog = dialog
end)

console:log("============================================")
console:log("[OpenClaw] Bridge v2.0 loaded!")
console:log("  Dialog detection: gTasks @ 0x03005E00, func=0x08098155")
console:log("  Player name: *gSaveBlock2Ptr + 0x00")
console:log("  Map info:    *gSaveBlock1Ptr + 0x04/0x05")
console:log("  IPC dir: " .. IPC_DIR)
console:log("============================================")
