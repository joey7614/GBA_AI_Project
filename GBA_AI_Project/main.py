"""
OpenClaw for GBA — Main AI Orchestrator
Communicates with mGBA via file IPC (bridge.lua handles the emulator side).

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

# ============================================================
# PATHS
# ============================================================
PROJECT_ROOT  = Path(__file__).parent
IPC_DIR       = PROJECT_ROOT / "ipc"
TRIGGER_FILE  = IPC_DIR / "trigger.json"
RESPONSE_FILE = IPC_DIR / "response.txt"
DB_PATH       = PROJECT_ROOT / "game_agent.db"
MEM_MAP_PATH  = PROJECT_ROOT / "memory_map.json"

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
        memory_type TEXT NOT NULL DEFAULT 'short_term',
        content     TEXT NOT NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS npc_affinity (
        npc_id  TEXT PRIMARY KEY,
        affinity INTEGER NOT NULL DEFAULT 0
    )""")
    conn.commit()
    return conn

def get_npc_memory(conn: sqlite3.Connection, npc_key: str) -> list:
    rows = conn.execute(
        "SELECT * FROM npc_memory WHERE npc_id = ? ORDER BY created_at DESC LIMIT 5",
        (npc_key,)
    ).fetchall()
    return [dict(r) for r in rows]

def get_npc_affinity(conn: sqlite3.Connection, npc_key: str) -> int:
    row = conn.execute(
        "SELECT affinity FROM npc_affinity WHERE npc_id = ?",
        (npc_key,)
    ).fetchone()
    return int(row["affinity"]) if row else 0

def save_interaction(conn: sqlite3.Connection, npc_key: str,
                     player_name: str, response: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO npc_affinity (npc_id, affinity) VALUES (?, 0)",
        (npc_key,)
    )
    conn.execute(
        "UPDATE npc_affinity SET affinity = affinity + 1 WHERE npc_id = ?",
        (npc_key,)
    )
    conn.execute(
        """INSERT INTO npc_memory (npc_id, memory_type, content)
           VALUES (?, 'short_term', ?)""",
        (npc_key, f"Said to {player_name}: {response}")
    )
    conn.commit()


# ============================================================
# AI RESPONSE GENERATION
# ============================================================
# Key = "map_group_map_num_pos_x_pos_y" (location-based NPC identity)
# Add entries here once you know which location is which NPC.
NPC_PERSONAS: dict[str, str] = {}

def build_prompt(npc_key: str, player_name: str,
                 memories: list, affinity: int) -> str:
    persona = NPC_PERSONAS.get(npc_key, f"a friendly NPC in Pokemon Emerald")
    memory_text = ""
    if memories:
        past = "\n".join(f"  - {m['content']}" for m in memories[-3:])
        memory_text = f"\nYour memories of this player:\n{past}"

    return f"""You are {persona} in Pokemon Emerald.
The player '{player_name}' just talked to you.
Your relationship score with this player: {affinity} (higher = friendlier).{memory_text}

Reply in 1-2 short sentences (max 60 characters total).
Stay in character. No quotes around your reply."""


def generate_response(model: genai.GenerativeModel, prompt: str) -> str:
    try:
        result = model.generate_content(prompt)
        text = result.text.strip()
        # Truncate hard at 75 chars to fit GBA text buffer safely
        if len(text) > 75:
            text = text[:72] + "..."
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
    print("[OpenClaw] Starting AI Orchestrator")
    print(f"  IPC dir : {IPC_DIR}")
    print(f"  Database: {DB_PATH}")
    print("=" * 50)

    model = init_gemini()
    conn  = get_db()

    print("[OpenClaw] Gemini ready. Waiting for NPC dialogs...")
    print("  Make sure mGBA has bridge.lua loaded!\n")

    while True:
        if TRIGGER_FILE.exists():
            try:
                raw  = TRIGGER_FILE.read_text(encoding="utf-8")
                data = json.loads(raw)
            except (json.JSONDecodeError, OSError) as e:
                print(f"[ERROR] Bad trigger file: {e}")
                TRIGGER_FILE.unlink(missing_ok=True)
                time.sleep(0.1)
                continue

            player_name = str(data.get("player_name", "Trainer"))
            map_group   = int(data.get("map_group", 0))
            map_num     = int(data.get("map_num", 0))
            pos_x       = int(data.get("pos_x", 0))
            pos_y       = int(data.get("pos_y", 0))
            # Use map+position as NPC key (no static NPC ID available yet)
            npc_key = f"{map_group}_{map_num}_{pos_x}_{pos_y}"
            print(f"\n[TRIGGER] npc_key={npc_key}  player='{player_name}'")

            # Load context from DB
            memories = get_npc_memory(conn, npc_key)
            affinity = get_npc_affinity(conn, npc_key)
            print(f"  Affinity: {affinity}  Past memories: {len(memories)}")

            # Build prompt and call Gemini
            prompt   = build_prompt(npc_key, player_name, memories, affinity)
            print("  Calling Gemini...")
            t0       = time.time()
            response = generate_response(model, prompt)
            elapsed  = time.time() - t0
            print(f"  Response ({elapsed:.1f}s): '{response}'")

            # Save to DB
            save_interaction(conn, npc_key, player_name, response)

            # Send to Lua
            RESPONSE_FILE.write_text(response, encoding="utf-8")
            TRIGGER_FILE.unlink(missing_ok=True)

        time.sleep(0.1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[OpenClaw] Stopped.")
