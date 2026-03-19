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

try:
    import yaml
except ImportError:
    print("ERROR: Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

from agent_tools import encode_text

# ============================================================
# PATHS
# ============================================================
PROJECT_ROOT      = Path(__file__).parent
IPC_DIR           = PROJECT_ROOT / "ipc"
REQUEST_FILE      = IPC_DIR / "request.json"
RESPONSE_FILE     = IPC_DIR / "response.json"
PLAYER_INPUT_FILE = IPC_DIR / "player_input_request.json"
DB_PATH           = PROJECT_ROOT / "game_agent.db"
CONFIG_FILE       = PROJECT_ROOT / "npc_config.yaml"

# ============================================================
# CONFIG  (loaded from npc_config.yaml)
# ============================================================
def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE} not found", file=sys.stderr)
        sys.exit(1)
    with CONFIG_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)

_cfg = load_config()

CONV_END_SECS:      float               = float(_cfg.get("conv_end_secs", 2.5))
SKIP_AI_MAPS:       set[tuple[int,int]] = {tuple(m) for m in _cfg.get("skip_ai_maps", [])}
NPC_PERSONALITY:    dict[str, str]      = {str(k): v for k, v in _cfg.get("npc_personality", {}).items()}
PERSONALITY_FILLERS: dict[str, list[str]] = _cfg.get("personality_fillers", {"DEFAULT": ["Hmm..."]})
RELATIONSHIP_CONTEXT: dict[str, str]   = _cfg.get("relationship_context", {})

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
        player_text TEXT,
        content     TEXT NOT NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS npc_affinity (
        npc_id   TEXT PRIMARY KEY,
        affinity INTEGER NOT NULL DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS npc_stats (
        npc_id           TEXT PRIMARY KEY,
        relationship     TEXT    NOT NULL DEFAULT 'stranger',
        -- Combat history
        battles_played   INTEGER NOT NULL DEFAULT 0,
        battles_lost     INTEGER NOT NULL DEFAULT 0,
        -- Trade history
        trades_count     INTEGER NOT NULL DEFAULT 0,
        pokemon_traded   TEXT    NOT NULL DEFAULT '[]',
        -- Gifts from player
        gifts_received   INTEGER NOT NULL DEFAULT 0,
        -- NPC's own progression
        pokemon_caught   INTEGER NOT NULL DEFAULT 0,
        gym_badges       INTEGER NOT NULL DEFAULT 0,
        gold             INTEGER NOT NULL DEFAULT 0,
        -- NPC inventory (small bag)
        potions          INTEGER NOT NULL DEFAULT 0,
        revives          INTEGER NOT NULL DEFAULT 0,
        special_item_1   TEXT,
        special_item_2   TEXT,
        special_item_3   TEXT,
        -- Player data (only used in prompt when relationship = rival or companion)
        player_items     TEXT,
        player_pokemon   TEXT,
        player_badges    INTEGER NOT NULL DEFAULT 0,
        updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    # Migrations
    try:
        conn.execute("ALTER TABLE npc_memory ADD COLUMN player_text TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    return conn

def get_npc_memory(conn: sqlite3.Connection, npc_key: str) -> list[dict]:
    rows = conn.execute(
        "SELECT player_text, content, created_at FROM npc_memory WHERE npc_id=? ORDER BY created_at DESC LIMIT 10",
        (npc_key,)
    ).fetchall()
    # Return in chronological order
    return [dict(r) for r in reversed(rows)]

def get_npc_affinity(conn: sqlite3.Connection, npc_key: str) -> int:
    row = conn.execute(
        "SELECT affinity FROM npc_affinity WHERE npc_id=?", (npc_key,)
    ).fetchone()
    return int(row["affinity"]) if row else 0

def get_npc_stats(conn: sqlite3.Connection, npc_key: str) -> dict:
    conn.execute(
        "INSERT OR IGNORE INTO npc_stats (npc_id) VALUES (?)", (npc_key,)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM npc_stats WHERE npc_id=?", (npc_key,)).fetchone()
    return dict(row)

def save_interaction(conn: sqlite3.Connection, npc_key: str, response: str,
                     player_text: str | None = None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO npc_affinity (npc_id, affinity) VALUES (?, 0)", (npc_key,)
    )
    conn.execute(
        "UPDATE npc_affinity SET affinity = affinity + 1 WHERE npc_id=?", (npc_key,)
    )
    conn.execute(
        "INSERT INTO npc_memory (npc_id, player_text, content) VALUES (?, ?, ?)",
        (npc_key, player_text, response)
    )
    conn.commit()


def get_filler(npc_key: str) -> str:
    personality = NPC_PERSONALITY.get(npc_key, "DEFAULT")
    options = PERSONALITY_FILLERS.get(personality) or PERSONALITY_FILLERS.get("DEFAULT", ["..."])
    return random.choice(options)


# ============================================================
# PLAYER INPUT  (Start menu "Ask NPC" → tkinter popup)
# ============================================================
_pending_player_text: str | None = None
_popup_open                      = False


def _show_input_popup() -> None:
    global _pending_player_text, _popup_open
    try:
        import tkinter as tk
        from tkinter import simpledialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        text = simpledialog.askstring(
            "Ask NPC",
            "What do you want to say?",
            parent=root,
        )
        root.destroy()
        if text and text.strip():
            with _cache_lock:
                _pending_player_text = text.strip()
            print(f"  [INPUT] Player question stored: '{text.strip()}'")
    except Exception as e:
        print(f"  [INPUT] Popup error: {e}", file=sys.stderr)
    finally:
        _popup_open = False


def open_input_popup() -> None:
    global _popup_open
    if _popup_open:
        return
    _popup_open = True
    threading.Thread(target=_show_input_popup, daemon=True).start()


# ============================================================
# AI RESPONSE
# ============================================================
def build_prompt(orig_text: str, memories: list[dict], affinity: int,
                 player_text: str | None = None,
                 stats: dict | None = None) -> str:
    # Extract character name from "NAME: text" pattern if present
    char_name = "an NPC"
    display_text = orig_text
    if ":" in orig_text:
        parts = orig_text.split(":", 1)
        char_name = parts[0].strip().title()
        display_text = parts[1].strip()

    s            = stats or {}
    relationship = s.get("relationship", "stranger")
    talk_count   = affinity  # affinity increments once per AI response

    # ---- Conversation history ----
    history_block = ""
    if memories:
        lines = []
        for m in memories:
            ts = m.get("created_at", "")[:16]  # "YYYY-MM-DD HH:MM"
            if m.get("player_text"):
                lines.append(f"  [{ts}] Player: {m['player_text']}")
            lines.append(f"  [{ts}] {char_name}: {m['content']}")
        history_block = (
            f"\n\nYou have spoken with this player {talk_count} time(s).\n"
            f"Conversation history:\n" + "\n".join(lines)
        )
    else:
        history_block = "\n\nThis is your first conversation with this player."

    # ---- NPC hidden metadata (always included, never shown to player) ----
    meta_lines = [f"[Your relationship with this player: {relationship}]"]
    if s.get("battles_played", 0) > 0:
        meta_lines.append(
            f"[Battles: {s['battles_played']} played, {s.get('battles_lost', 0)} lost by you]"
        )
    if s.get("trades_count", 0) > 0:
        traded = s.get("pokemon_traded", "[]")
        meta_lines.append(f"[Trades: {s['trades_count']} trade(s), Pokemon: {traded}]")
    if s.get("gifts_received", 0) > 0:
        meta_lines.append(f"[Gifts received from player: {s['gifts_received']}]")
    npc_inv = []
    if s.get("potions", 0):   npc_inv.append(f"{s['potions']} potion(s)")
    if s.get("revives", 0):   npc_inv.append(f"{s['revives']} revive(s)")
    for slot in ("special_item_1", "special_item_2", "special_item_3"):
        if s.get(slot):       npc_inv.append(s[slot])
    if npc_inv:
        meta_lines.append(f"[Your inventory: {', '.join(npc_inv)}]")
    if s.get("gym_badges", 0) or s.get("gold", 0):
        meta_lines.append(f"[Your gym badges: {s.get('gym_badges', 0)}, gold: {s.get('gold', 0)}]")
    meta_block = "\n" + "\n".join(meta_lines)

    # ---- Player data — only for rival / companion ----
    player_data_block = ""
    if relationship in ("rival", "companion"):
        pd_lines = []
        if s.get("player_badges", 0):
            pd_lines.append(f"Player's gym badges: {s['player_badges']}")
        if s.get("player_pokemon"):
            pd_lines.append(f"Player's Pokemon: {s['player_pokemon']}")
        if s.get("player_items"):
            pd_lines.append(f"Player's items: {s['player_items']}")
        if pd_lines:
            rel_context = RELATIONSHIP_CONTEXT.get(relationship, "")
            player_data_block = (
                f"\n[{rel_context}]\n"
                f"[Player profile]\n" + "\n".join(f"  {l}" for l in pd_lines)
            )

    # ---- Current question ----
    player_block = ""
    if player_text:
        player_block = f"\n\nThe player just asked you: \"{player_text}\""

    instruction = (
        "Respond directly to what the player asked. Be expressive but BRIEF — "
        if player_text else
        "Respond naturally AS this character, continuing the conversation. Be expressive but BRIEF — "
    )

    return (
        f"You are {char_name} in Pokemon FireRed. Speak as this character — "
        f"with real emotion, personality, and warmth. Sound like a person, not a robot.\n\n"
        f"The game script has you say: \"{display_text[:100]}\"\n"
        f"{history_block}\n{meta_block}{player_data_block}{player_block}\n\n"
        f"{instruction}"
        f"max 2-3 short sentences, under 100 characters total. "
        f"Plain ASCII only (letters, numbers, spaces, . , ! ? - '). "
        f"No quotes around your reply."
    )

def word_wrap(text: str, line_width: int = 18, max_pages: int = 3) -> str:
    """Wrap text into GBA dialog pages (18 chars/line, 2 lines/page, multi-page).

    Pages are separated by \\f (0xFB = CHAR_PROMPT_CLEAR — waits for A, clears box).
    """
    words = text.split()
    pages: list[str] = []
    page_lines: list[str] = []
    current = ""

    for word in words:
        test = (current + " " + word).strip()
        if len(test) <= line_width:
            current = test
        else:
            if current:
                page_lines.append(current)
            if len(page_lines) >= 2:          # page full (2 lines)
                pages.append("\n".join(page_lines))
                page_lines = []
            current = word[:line_width]

    if current:
        page_lines.append(current)
    if page_lines:
        pages.append("\n".join(page_lines))

    return "\f".join(pages[:max_pages])


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
_npc_cache:      dict[str, list[int]]       = {}
_generating:     set[str]                   = set()
_question_waiting: set[str]                 = set()   # NPCs that just got an acknowledgement — next talk = filler
_conv_timers:    dict[str, threading.Timer] = {}
_cache_lock                                 = threading.Lock()

CONV_END_SECS = 2.5   # seconds of silence before we consider a conversation over


def _background_generate(
    model:       genai.GenerativeModel,
    npc_key:     str,
    orig_text:   str,
    player_text: str | None = None,
) -> None:
    try:
        conn     = get_db()          # each thread gets its own connection
        memories = get_npc_memory(conn, npc_key)
        affinity = get_npc_affinity(conn, npc_key)
        stats    = get_npc_stats(conn, npc_key)
        prompt   = build_prompt(orig_text, memories, affinity, player_text, stats)
        print(f"  [BG:{npc_key}] Generating..." + (f" (player asked: '{player_text}')" if player_text else ""))
        t0       = time.time()
        response = generate_response(model, prompt)
        elapsed  = time.time() - t0
        if response is None:
            print(f"  [BG:{npc_key}] Failed ({elapsed:.1f}s) — not caching")
            conn.close()
            return
        print(f"  [BG:{npc_key}] Done ({elapsed:.1f}s): '{response}'")
        save_interaction(conn, npc_key, response, player_text)
        conn.close()
        ai_bytes = encode_text(response, max_len=126)
        with _cache_lock:
            _npc_cache[npc_key] = ai_bytes
    finally:
        with _cache_lock:
            _generating.discard(npc_key)


def _kick_background(
    model:       genai.GenerativeModel,
    npc_key:     str,
    orig_text:   str,
    player_text: str | None = None,
) -> None:
    with _cache_lock:
        if npc_key in _generating:
            if player_text:
                # A question was asked — spawn a waiter thread so it doesn't get dropped
                def _wait_then_generate():
                    while True:
                        with _cache_lock:
                            if npc_key not in _generating:
                                _generating.add(npc_key)
                                break
                        time.sleep(0.1)
                    _background_generate(model, npc_key, orig_text, player_text)
                threading.Thread(target=_wait_then_generate, daemon=True).start()
            return
        _generating.add(npc_key)
    threading.Thread(
        target=_background_generate,
        args=(model, npc_key, orig_text, player_text),
        daemon=True,
    ).start()


def _schedule_prefetch(
    model:       genai.GenerativeModel,
    npc_key:     str,
    orig_text:   str,
    player_text: str | None = None,
) -> None:
    """Debounced background kick — resets on each new message in the same conversation."""
    with _cache_lock:
        old = _conv_timers.pop(npc_key, None)
    if old:
        old.cancel()
    t = threading.Timer(CONV_END_SECS, _kick_background, args=(model, npc_key, orig_text, player_text))
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
        # ---- Poll for player input request (Start menu "Ask NPC") ----
        if PLAYER_INPUT_FILE.exists():
            PLAYER_INPUT_FILE.unlink(missing_ok=True)
            print("[INPUT] Player pressed Ask NPC — opening popup")
            open_input_popup()

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

            # Check if player has a pending question
            global _pending_player_text
            with _cache_lock:
                player_text = _pending_player_text
                if player_text:
                    _pending_player_text = None
                    # Clear any cached response — AI must answer the question fresh
                    _npc_cache.pop(npc_key, None)

            if player_text:
                # Player asked a question — echo it back so they can confirm receipt,
                # then kick AI generation with the question.
                print(f"  [QUESTION] Player asked: '{player_text}'")
                preview = player_text[:17]   # fits one GBA line
                filler  = get_filler(npc_key)
                ack_text = f"{preview}\n{filler}"
                ack_bytes = encode_text(ack_text, max_len=126)
                RESPONSE_FILE.write_text(
                    json.dumps({"ai_bytes": ack_bytes}), encoding="utf-8"
                )
                with _cache_lock:
                    _question_waiting.add(npc_key)
                _kick_background(model, npc_key, orig_text, player_text)
            else:
                with _cache_lock:
                    in_waiting = npc_key in _question_waiting
                    if in_waiting:
                        _question_waiting.discard(npc_key)

                if in_waiting:
                    # Just showed acknowledgement — always show filler this turn
                    # so the player feels the NPC is thinking before answering.
                    filler = get_filler(npc_key)
                    print(f"  [THINKING] Post-question filler: '{filler}'")
                    filler_bytes = encode_text(filler, max_len=126)
                    RESPONSE_FILE.write_text(
                        json.dumps({"ai_bytes": filler_bytes}), encoding="utf-8"
                    )
                else:
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
