-- OpenClaw Bridge
-- Runs inside mGBA: Tools -> Scripting -> Load Script
--
-- IPC protocol (matches gOpenClawIPC struct at 0x0203F468):
--   +0   request_ready  (u8) — ROM sets 1 when NPC dialog fires
--   +1   response_ready (u8) — Lua sets 1 when AI text is ready
--   +2   pad[2]
--   +4   npc_orig_text[128]  — Gen3 bytes of original NPC line
--   +132 ai_text[128]        — Lua writes Gen3 bytes from Python here
--
-- File protocol:
--   ipc/request.json  — Lua writes when request_ready==1
--   ipc/response.json — Python writes {"ai_bytes":[...]} Gen3 byte list

-- ============================================================
-- CONFIG
-- ============================================================
local IPC_BASE      = 0x0203F468
local IPC_DIR       = "C:/Users/linji/Documents/GBA_AI_Project/GBA_AI_Project/ipc/"
local REQUEST_FILE  = IPC_DIR .. "request.json"
local RESPONSE_FILE = IPC_DIR .. "response.json"

local EOS      = 0xFF
local TEXT_MAX = 127

-- ============================================================
-- GAME STATE READERS  (SaveBlock pointers — confirmed Fire Red)
-- ============================================================
local SAVEBLOCK1_PTR = 0x03005D8C
local SAVEBLOCK2_PTR = 0x03005D90

local function get_map_info()
    local sb1 = emu:read32(SAVEBLOCK1_PTR)
    if sb1 == 0 then return 0, 0 end
    local map_group = emu:read8(sb1 + 0x04)
    local map_num   = emu:read8(sb1 + 0x05)
    return map_group, map_num
end

-- ============================================================
-- MEMORY HELPERS
-- ============================================================
local function read_gen3_bytes(base_addr)
    local bytes = {}
    for i = 0, TEXT_MAX - 1 do
        local b = emu:read8(base_addr + i)
        if b == EOS then break end
        bytes[#bytes + 1] = b
    end
    return bytes
end

local function write_gen3_bytes(base_addr, bytes)
    for i, b in ipairs(bytes) do
        emu:write8(base_addr + i - 1, b)
    end
    emu:write8(base_addr + #bytes, EOS)
end

-- ============================================================
-- FILE HELPERS
-- ============================================================
local function file_exists(path)
    local f = io.open(path, "r")
    if f then f:close() return true end
    return false
end

local function write_file(path, content)
    local f = io.open(path, "w")
    if not f then
        console:log("[OpenClaw] ERROR: cannot write " .. path)
        return false
    end
    f:write(content)
    f:close()
    return true
end

local function read_file(path)
    local f = io.open(path, "r")
    if not f then return nil end
    local c = f:read("*a")
    f:close()
    return c
end

-- ============================================================
-- JSON HELPERS
-- ============================================================
local function bytes_to_json_array(bytes)
    local parts = {}
    for _, b in ipairs(bytes) do
        parts[#parts + 1] = tostring(b)
    end
    return "[" .. table.concat(parts, ",") .. "]"
end

-- Parse {"ai_bytes": [1,2,3,...]} from response.json
local function parse_ai_bytes(content)
    local arr_str = content:match('"ai_bytes"%s*:%s*(%[.-%])')
    if not arr_str then return nil end
    local bytes = {}
    for num in arr_str:gmatch("%d+") do
        bytes[#bytes + 1] = tonumber(num)
    end
    return bytes
end

-- ============================================================
-- MAIN FRAME LOOP
-- ============================================================
local waiting      = false
local wait_frames  = 0
local TIMEOUT      = 600   -- 10 seconds at 60fps

callbacks:add("frame", function()

    -- ---- Check request from ROM ----
    if not waiting and emu:read8(IPC_BASE) == 1 then
        local orig_bytes = read_gen3_bytes(IPC_BASE + 4)
        local mg, mn     = get_map_info()

        local json = string.format(
            '{"npc_orig_bytes":%s,"map_group":%d,"map_num":%d}',
            bytes_to_json_array(orig_bytes), mg, mn
        )

        if write_file(REQUEST_FILE, json) then
            emu:write8(IPC_BASE, 0)   -- clear request_ready
            waiting     = true
            wait_frames = 0
            console:log(string.format(
                "[OpenClaw] Request sent — %d bytes, map %d/%d",
                #orig_bytes, mg, mn))
        end
    end

    -- ---- Poll for Python response ----
    if waiting then
        wait_frames = wait_frames + 1

        if file_exists(RESPONSE_FILE) then
            local content = read_file(RESPONSE_FILE)
            os.remove(RESPONSE_FILE)

            local ai_bytes = content and parse_ai_bytes(content)
            if ai_bytes and #ai_bytes > 0 then
                write_gen3_bytes(IPC_BASE + 132, ai_bytes)
                emu:write8(IPC_BASE + 1, 1)   -- set response_ready
                -- Debug: log all bytes to verify 0xFE (254) newline is present
                local byteLog = "[OpenClaw] Bytes: "
                local has_newline = false
                for i, b in ipairs(ai_bytes) do
                    byteLog = byteLog .. string.format("%02X ", b)
                    if b == 0xFE then has_newline = true end
                end
                console:log(byteLog)
                if has_newline then
                    console:log("[OpenClaw] Has 0xFE newline — 2-line text expected")
                end
                console:log(string.format(
                    "[OpenClaw] Response injected — %d bytes", #ai_bytes))
            else
                console:log("[OpenClaw] ERROR: bad response.json")
            end
            waiting = false
        end

        if wait_frames >= TIMEOUT then
            console:log("[OpenClaw] TIMEOUT waiting for Python")
            waiting     = false
            wait_frames = 0
        end
    end

end)

console:log("============================================")
console:log("[OpenClaw] Bridge loaded")
console:log("  IPC_BASE  = 0x0203F468")
console:log("  IPC dir   = " .. IPC_DIR)
console:log("============================================")
