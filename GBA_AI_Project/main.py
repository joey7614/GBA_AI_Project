"""
OpenClaw — Main AI Orchestrator
Watches ipc/request.json, calls Gemini, writes ipc/response.json.

Run:  python main.py
Stop: Ctrl+C
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

try:
    import google.generativeai as genai
except ImportError:
    print("ERROR: Run: pip install google-generativeai", file=sys.stderr)
    sys.exit(1)

from agent_tools import encode_text

# ============================================================
# PATHS
# ============================================================
PROJECT_ROOT  = Path(__file__).parent
IPC_DIR       = PROJECT_ROOT / "ipc"
REQUEST_FILE  = IPC_DIR / "request.json"
RESPONSE_FILE = IPC_DIR / "response.json"
DB_PATH       = PROJECT_ROOT / "game_agent.db"

# ============================================================
# GEN3 DECODE  (bytes → readable ASCII for the prompt)
# ============================================================
_GEN3_TO_ASCII: dict[int, str] = {0x00: " "}
for _i in range(26):
    _GEN3_TO_ASCII[0xBB + _i] = chr(65 + _i)   # A-Z
    _GEN3_TO_ASCII[0xD5 + _i] = chr(97 + _i)   # a-z
for _i in range(10):
    _GEN3_TO_ASCII[0xA1 + _i] = chr(48 + _i)   # 0-9
_GEN3_TO_ASCII.update({
    0xAB: "!", 0xAC: "?", 0xAD: ".", 0xAE: "-",
    0xB8: ",", 0xB4: "'", 0xF0: ":", 0xFE: "\n",
})

def decode_gen3(byte_list: list[int]) -> str:
    return "".join(_GEN3_TO_ASCII.get(b, "?") for b in byte_list if b != 0xFF)


# ============================================================
# GEMINI SETUP
# ============================================================
def init_gemini() -> genai.GenerativeModel:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-2.5-flash")


# ============================================================
# DATABASE
# ============================================================
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS npc_memory (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        npc_id      TEXT NOT NULL,
        content     TEXT NOT NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS npc_affinity (
        npc_id   TEXT PRIMARY KEY,
        affinity INTEGER NOT NULL DEFAULT 0
    )""")
    conn.commit()
    return conn

def get_npc_memory(conn: sqlite3.Connection, npc_key: str) -> list[dict]:
    rows = conn.execute(
        "SELECT content FROM npc_memory WHERE npc_id=? ORDER BY created_at DESC LIMIT 5",
        (npc_key,)
    ).fetchall()
    return [dict(r) for r in rows]

def get_npc_affinity(conn: sqlite3.Connection, npc_key: str) -> int:
    row = conn.execute(
        "SELECT affinity FROM npc_affinity WHERE npc_id=?", (npc_key,)
    ).fetchone()
    return int(row["affinity"]) if row else 0

def save_interaction(conn: sqlite3.Connection, npc_key: str, response: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO npc_affinity (npc_id, affinity) VALUES (?, 0)", (npc_key,)
    )
    conn.execute(
        "UPDATE npc_affinity SET affinity = affinity + 1 WHERE npc_id=?", (npc_key,)
    )
    conn.execute(
        "INSERT INTO npc_memory (npc_id, content) VALUES (?, ?)",
        (npc_key, response)
    )
    conn.commit()


# ============================================================
# STORY NPC FILTER
# Maps listed here pass through original dialog unchanged.
# Add (map_group, map_num) tuples for any story-critical map.
# Find map IDs by watching the [REQUEST] log when you talk to NPCs.
# ============================================================
SKIP_AI_MAPS: set[tuple[int, int]] = {
    # Add (map_group, map_num) here to preserve original dialog for story NPCs
    # Example: (0, 0) is Player's house — Mom/intro sequence
}


# ============================================================
# AI RESPONSE
# ============================================================
def build_prompt(orig_text: str, memories: list[dict], affinity: int) -> str:
    # Extract character name from "NAME: text" pattern if present
    char_name = "an NPC"
    display_text = orig_text
    if ":" in orig_text:
        parts = orig_text.split(":", 1)
        char_name = parts[0].strip().title()
        display_text = parts[1].strip()

    memory_block = ""
    if memories:
        past = "\n".join(f"  - {m['content']}" for m in memories[-3:])
        memory_block = f"\n\nYou've spoken to this player before:\n{past}"

    bond = "stranger" if affinity == 0 else ("acquaintance" if affinity < 5 else "friend")

    return (
        f"You are {char_name} in Pokemon FireRed. Speak as this character — "
        f"with real emotion, personality, and warmth. Sound like a person, not a robot.\n\n"
        f"The game script has you say: \"{display_text[:100]}\"\n"
        f"This player is your {bond} (bond level {affinity}).{memory_block}\n\n"
        f"Respond naturally AS this character. Be expressive. 1-2 sentences max. "
        f"Under 100 characters. Plain ASCII only (letters, numbers, spaces, . , ! ? - '). "
        f"No quotes around your reply."
    )

def generate_response(model: genai.GenerativeModel, prompt: str) -> str:
    try:
        result = model.generate_content(prompt)
        text = result.text.strip()
        if len(text) > 100:
            text = text[:97] + "..."
        return text
    except Exception as e:
        print(f"[AI] Gemini error: {e}", file=sys.stderr)
        return "..."


# ============================================================
# MAIN LOOP
# ============================================================
def main() -> None:
    IPC_DIR.mkdir(exist_ok=True)

    print("=" * 50)
    print("[OpenClaw] Starting")
    print(f"  IPC dir : {IPC_DIR}")
    print(f"  Database: {DB_PATH}")
    print("=" * 50)

    model = init_gemini()
    conn  = get_db()

    print("[OpenClaw] Ready. Watching for NPC requests...")
    print("  Make sure mGBA has bridge.lua loaded!\n")

    while True:
        if REQUEST_FILE.exists():
            try:
                data = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
                REQUEST_FILE.unlink(missing_ok=True)
            except (json.JSONDecodeError, OSError) as e:
                print(f"[ERROR] Bad request.json: {e}")
                REQUEST_FILE.unlink(missing_ok=True)
                time.sleep(0.1)
                continue

            orig_bytes  = data.get("npc_orig_bytes", [])
            map_group   = int(data.get("map_group", 0))
            map_num     = int(data.get("map_num", 0))
            orig_text   = decode_gen3(orig_bytes)
            npc_key     = f"{map_group}_{map_num}"

            print(f"\n[REQUEST] map={map_group}/{map_num}  orig='{orig_text}'")

            # Story NPC filter: pass through original dialog unchanged
            if (map_group, map_num) in SKIP_AI_MAPS:
                print(f"  [SKIP] Story map {map_group}/{map_num} — passing through original")
                ai_bytes = orig_bytes + [0xFF]
                RESPONSE_FILE.write_text(
                    json.dumps({"ai_bytes": ai_bytes}), encoding="utf-8"
                )
                continue

            memories = get_npc_memory(conn, npc_key)
            affinity = get_npc_affinity(conn, npc_key)
            print(f"  Affinity: {affinity}  Memories: {len(memories)}")

            prompt   = build_prompt(orig_text, memories, affinity)
            print("  Calling Gemini...")
            t0       = time.time()
            response = generate_response(model, prompt)
            elapsed  = time.time() - t0
            print(f"  Response ({elapsed:.1f}s): '{response}'")

            save_interaction(conn, npc_key, response)

            ai_bytes = encode_text(response, max_len=126)
            RESPONSE_FILE.write_text(
                json.dumps({"ai_bytes": ai_bytes}),
                encoding="utf-8"
            )
            print(f"  Wrote response.json ({len(ai_bytes)} bytes incl EOS)")

        time.sleep(0.05)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[OpenClaw] Stopped.")
