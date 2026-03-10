"""
Automated memory scanner — finds dialog_state address in Pokemon Emerald.

SETUP:
  1. mGBA: Tools -> Start GDB Server -> Port 2345 -> OK
  2. Make sure the game is running (not paused)
  3. Run this script: python find_dialog.py

The script will:
  - Connect to mGBA
  - Scan IWRAM baseline (no dialog open)
  - Prompt you to open a dialog in-game
  - Re-scan and report which addresses changed 0 -> nonzero
"""

import time
from gdb_client import GDBClient

# GBA IWRAM: 0x03000000 - 0x03007FFF (32 KB)
IWRAM_START = 0x03000000
IWRAM_SIZE  = 0x8000   # 32 KB
CHUNK       = 256      # bytes per GDB read


def scan_region(client: GDBClient, start: int, size: int) -> dict[int, int]:
    """Read a memory region in chunks, return {addr: value} dict."""
    snapshot = {}
    for offset in range(0, size, CHUNK):
        addr = start + offset
        chunk_size = min(CHUNK, size - offset)
        data = client.read_memory(addr, chunk_size)
        if data:
            for i, b in enumerate(data):
                snapshot[addr + i] = b
    return snapshot


def diff_snapshots(before: dict, after: dict) -> list[tuple[int, int, int]]:
    """Return list of (addr, before_val, after_val) where value increased from 0."""
    changes = []
    for addr, val_before in before.items():
        val_after = after.get(addr, 0)
        if val_before == 0 and val_after != 0:
            changes.append((addr, val_before, val_after))
    return changes


def main():
    client = GDBClient()
    if not client.connect():
        print("\nERROR: Could not connect to mGBA.")
        print("Make sure: Tools -> Start GDB Server (port 2345) is running.")
        return

    print("\n" + "=" * 55)
    print("  OpenClaw Memory Scanner")
    print("=" * 55)
    print(f"Scanning IWRAM ({IWRAM_SIZE // 1024}KB)...")

    # --- Baseline: no dialog open ---
    print("\nStep 1: Make sure NO dialog box is open in-game.")
    input("Press ENTER when ready (no dialog on screen)...")
    print("Taking baseline snapshot...")
    baseline = scan_region(client, IWRAM_START, IWRAM_SIZE)
    print(f"  Captured {len(baseline)} bytes.")

    # --- Open dialog ---
    print("\nStep 2: Go talk to an NPC — open any dialog box.")
    print("        Keep the dialog open (don't press A to close it).")
    input("Press ENTER when a dialog box IS visible on screen...")

    print("Scanning with dialog open...")
    with_dialog = scan_region(client, IWRAM_START, IWRAM_SIZE)

    # --- Find candidates ---
    changes = diff_snapshots(baseline, with_dialog)
    changes.sort(key=lambda x: x[0])

    print(f"\nAddresses that changed 0 -> nonzero ({len(changes)} found):")
    print("-" * 45)
    for addr, v_before, v_after in changes:
        print(f"  0x{addr:08X}  :  {v_before} -> {v_after} (0x{v_after:02X})")

    if not changes:
        print("  No changes found. Try again — make sure dialog is OPEN")
        print("  when you press Enter the second time.")
        client.close()
        return

    # --- Verify: close dialog and re-read candidates ---
    print("\nStep 3: Now CLOSE the dialog (press B or A).")
    input("Press ENTER after closing the dialog...")

    print("\nVerifying which addresses went back to 0...")
    print("-" * 45)
    confirmed = []
    for addr, _, v_open in changes:
        v_closed = client.read_u8(addr)
        status = "CONFIRMED dialog_state" if v_closed == 0 else f"stayed {v_closed}"
        print(f"  0x{addr:08X}  open={v_open}  closed={v_closed}  <- {status}")
        if v_closed == 0:
            confirmed.append((addr, v_open))

    print("\n" + "=" * 55)
    if confirmed:
        print("CANDIDATES (go back to 0 when dialog closes):")
        for addr, v in confirmed:
            print(f"  0x{addr:08X}  (opens to {v})")
        best = confirmed[0][0]
        print(f"\nBest guess for dialog_state: 0x{best:08X}")
        print(f'\nUpdate memory_map.json -> "dialog_state": "0x{best:08X}"')
    else:
        print("No clean 0->X->0 addresses found. The dialog state")
        print("might use a different pattern (not a simple 0/1 flag).")

    client.close()


if __name__ == "__main__":
    main()
