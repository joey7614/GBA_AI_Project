"""
OpenClaw — Main AI Orchestrator
Watches ipc/request.json, calls Gemini, writes ipc/response.json.

Run:  python main.py
Stop: Ctrl+C
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
import threading
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
# NPC PERSONALITY & THINKING FILLERS
# Shown when player re-talks to an NPC while AI is still generating.
# ============================================================
PERSONALITY_FILLERS: dict[str, list[str]] = {
    "MOTHERLY": [
        "Hmm...",
        "Oh my...",
        "Now, let me think...",
        "Oh, sweetie...",
        "My, my...",
        "Well...",
    ],
    "GRUFF": [
        "Hmph.",
        "...",
        "Bah.",
        "Hold on.",
        "Hm.",
    ],
    "CHEERFUL": [
        "Ooh!",
        "Umm...",
        "Oh!",
        "Heehee...",
        "Hmm!",
    ],
    "SCHOLARLY": [
        "Fascinating...",
        "Hmm, yes...",
        "Interesting...",
        "Let me see...",
        "Ah...",
    ],
    "NERVOUS": [
        "U-um...",
        "Uh...",
        "W-well...",
        "Eek, uh...",
    ],
    "MYSTERIOUS": [
        "...",
        "Curious...",
        "I see...",
        "Mmm...",
    ],
    "DEFAULT": [
        "Hmm...",
        "...",
        "Let me think...",
    ],
}

# npc_key ("{map_group}_{map_num}") → personality type
# Add entries here as you identify NPCs via the [REQUEST] log.
NPC_PERSONALITY: dict[str, str] = {
    "0_0": "MOTHERLY",   # Pallet Town Player's House 1F — Mom
}


def get_filler(npc_key: str) -> str:
    personality = NPC_PERSONALITY.get(npc_key, "DEFAULT")
    return random.choice(PERSONALITY_FILLERS[personality])


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
        f"Respond naturally AS this character. Be expressive but BRIEF — "
        f"max 1 short sentence, under 35 characters. "
        f"Plain ASCII only (letters, numbers, spaces, . , ! ? - '). "
        f"No quotes around your reply."
    )

def word_wrap(text: str, line_width: int = 18) -> str:
    """Wrap text to fit GBA dialog box (18 chars/line, 2 lines per page)."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = (current + " " + word).strip()
        if len(test) <= line_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word[:line_width]
    if current:
        lines.append(current)
    return "\n".join(lines[:2])  # max 2 lines per dialog box


def generate_response(model: genai.GenerativeModel, prompt: str) -> str | None:
    try:
        result = model.generate_content(prompt)
        text = result.text.strip()
        text = word_wrap(text)
        return text
    except Exception as e:
        print(f"[AI] Gemini error: {e}", file=sys.stderr)
        return None


# ============================================================
# PRE-FETCH CACHE  (per-NPC, thread-safe)
# ============================================================
# _npc_cache[npc_key]  = pre-generated ai_bytes ready for next talk
# _generating          = npc_keys whose background thread is running
# _conv_timers[npc_key]= debounce timer — fires after conversation goes quiet
# Generation is delayed CONV_END_SECS after the last message of a conversation,
# so multi-message conversations don't trigger premature generation.
_npc_cache:   dict[str, list[int]]          = {}
_generating:  set[str]                      = set()
_conv_timers: dict[str, threading.Timer]    = {}
_cache_lock                                 = threading.Lock()

CONV_END_SECS = 2.5   # seconds of silence before we consider a conversation over


def _background_generate(
    model:     genai.GenerativeModel,
    npc_key:   str,
    orig_text: str,
) -> None:
    try:
        conn     = get_db()          # each thread gets its own connection
        memories = get_npc_memory(conn, npc_key)
        affinity = get_npc_affinity(conn, npc_key)
        prompt   = build_prompt(orig_text, memories, affinity)
        print(f"  [BG:{npc_key}] Generating...")
        t0       = time.time()
        response = generate_response(model, prompt)
        elapsed  = time.time() - t0
        if response is None:
            print(f"  [BG:{npc_key}] Failed ({elapsed:.1f}s) — not caching")
            conn.close()
            return
        print(f"  [BG:{npc_key}] Done ({elapsed:.1f}s): '{response}'")
        save_interaction(conn, npc_key, response)
        conn.close()
        ai_bytes = encode_text(response, max_len=126)
        with _cache_lock:
            _npc_cache[npc_key] = ai_bytes
    finally:
        with _cache_lock:
            _generating.discard(npc_key)


def _kick_background(
    model:     genai.GenerativeModel,
    npc_key:   str,
    orig_text: str,
) -> None:
    with _cache_lock:
        if npc_key in _generating:
            return
        _generating.add(npc_key)
    threading.Thread(
        target=_background_generate,
        args=(model, npc_key, orig_text),
        daemon=True,
    ).start()


def _schedule_prefetch(
    model:     genai.GenerativeModel,
    npc_key:   str,
    orig_text: str,
) -> None:
    """Debounced background kick — resets on each new message in the same conversation."""
    with _cache_lock:
        old = _conv_timers.pop(npc_key, None)
    if old:
        old.cancel()
    t = threading.Timer(CONV_END_SECS, _kick_background, args=(model, npc_key, orig_text))
    with _cache_lock:
        _conv_timers[npc_key] = t
    t.start()
    print(f"  [PREFETCH:{npc_key}] Scheduled in {CONV_END_SECS}s")


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

            orig_bytes = data.get("npc_orig_bytes", [])
            map_group  = int(data.get("map_group", 0))
            map_num    = int(data.get("map_num", 0))
            orig_text  = decode_gen3(orig_bytes)
            npc_key    = f"{map_group}_{map_num}"

            print(f"\n[REQUEST] map={map_group}/{map_num}  orig='{orig_text}'")

            # Story NPC filter: pass through original dialog unchanged
            if (map_group, map_num) in SKIP_AI_MAPS:
                print(f"  [SKIP] Story map {map_group}/{map_num} — passing through original")
                ai_bytes = orig_bytes + [0xFF]
                RESPONSE_FILE.write_text(
                    json.dumps({"ai_bytes": ai_bytes}), encoding="utf-8"
                )
                continue

            with _cache_lock:
                cached = _npc_cache.pop(npc_key, None)

            if cached is not None:
                # Pre-generated response ready — serve it immediately
                print(f"  [CACHE HIT] Serving pre-generated response for {npc_key}")
                RESPONSE_FILE.write_text(
                    json.dumps({"ai_bytes": cached}), encoding="utf-8"
                )
                # Kick off generation of the NEXT response in background
                _schedule_prefetch(model, npc_key, orig_text)
            else:
                with _cache_lock:
                    still_generating = npc_key in _generating

                if still_generating:
                    # Generation in flight — player re-talked before it finished.
                    # Serve a personality filler instead of repeating original text.
                    filler = get_filler(npc_key)
                    print(f"  [FILLER] Still generating, serving: '{filler}'")
                    filler_bytes = encode_text(filler, max_len=126)
                    RESPONSE_FILE.write_text(
                        json.dumps({"ai_bytes": filler_bytes}), encoding="utf-8"
                    )
                else:
                    # First time talking to this NPC — serve original text instantly
                    print(f"  [FIRST TALK] Serving original text, scheduling prefetch")
                    ai_bytes = orig_bytes + [0xFF]
                    RESPONSE_FILE.write_text(
                        json.dumps({"ai_bytes": ai_bytes}), encoding="utf-8"
                    )
                    # Generate first AI response in background for next talk
                    _schedule_prefetch(model, npc_key, orig_text)

        time.sleep(0.05)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[OpenClaw] Stopped.")
