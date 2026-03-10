"""
Step 3: Test IPC without AI.
Run this while mGBA is running with bridge.lua loaded.
When you talk to an NPC in-game, this script reads the trigger
and writes a hardcoded reply back — no Gemini needed yet.

Run:  python test_ipc.py
Stop: Ctrl+C
"""

import json
import os
import time
from pathlib import Path

IPC_DIR      = Path(__file__).parent / "ipc"
TRIGGER_FILE = IPC_DIR / "trigger.json"
RESPONSE_FILE = IPC_DIR / "response.txt"
STATUS_FILE  = IPC_DIR / "status.txt"

FAKE_RESPONSES = [
    "Hello trainer! I am an AI controlled NPC.",
    "The weather is nice today in Littleroot!",
    "I heard Professor Birch is looking for you.",
    "Be careful on Route 101, the grass is wild!",
]
response_index = 0


def main():
    global response_index

    IPC_DIR.mkdir(exist_ok=True)
    print("=" * 50)
    print("[test_ipc] Polling for triggers...")
    print(f"  Trigger : {TRIGGER_FILE}")
    print(f"  Response: {RESPONSE_FILE}")
    print("  Open mGBA, load bridge.lua, talk to an NPC.")
    print("=" * 50)

    while True:
        # Check for trigger
        if TRIGGER_FILE.exists():
            try:
                raw = TRIGGER_FILE.read_text(encoding="utf-8")
                data = json.loads(raw)
                print(f"\n[TRIGGER] npc_id={data.get('npc_id')}  "
                      f"player='{data.get('player_name')}'  "
                      f"frame={data.get('frame')}")

                # Simulate AI thinking (1 second delay)
                print("[test_ipc] Simulating AI response (1s delay)...")
                time.sleep(1.0)

                # Pick a fake response
                reply = FAKE_RESPONSES[response_index % len(FAKE_RESPONSES)]
                response_index += 1

                # Write response for Lua
                RESPONSE_FILE.write_text(reply, encoding="utf-8")
                TRIGGER_FILE.unlink(missing_ok=True)

                print(f"[test_ipc] Response sent: '{reply}'")

            except (json.JSONDecodeError, OSError) as e:
                print(f"[test_ipc] Error reading trigger: {e}")
                TRIGGER_FILE.unlink(missing_ok=True)

        # Print status if available
        if STATUS_FILE.exists():
            try:
                status = json.loads(STATUS_FILE.read_text())
                waiting = status.get("waiting", False)
                if waiting:
                    print(f"  [status] frame={status['frame']}  "
                          f"waiting_for_response=True", end="\r")
            except (json.JSONDecodeError, OSError):
                pass

        time.sleep(0.1)  # poll at 10Hz


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[test_ipc] Stopped.")
