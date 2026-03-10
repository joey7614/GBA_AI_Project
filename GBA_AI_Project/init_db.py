from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS npc_affinity (
  npc_id TEXT PRIMARY KEY,
  npc_name TEXT,
  affinity INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS npc_memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  npc_id TEXT NOT NULL,
  memory TEXT NOT NULL,
  importance INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  meta_json TEXT,
  FOREIGN KEY (npc_id) REFERENCES npc_affinity(npc_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_npc_memory_npc_id_created_at
  ON npc_memory (npc_id, created_at);
"""


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.executescript(SCHEMA_SQL)


def _parse_args() -> argparse.Namespace:
    default_db = Path(__file__).with_name("game_agent.db")
    default_db = Path(os.getenv("OPENCLAW_DB_PATH", str(default_db)))

    parser = argparse.ArgumentParser(description="Initialize OpenClaw for GBA SQLite database.")
    parser.add_argument("--db", type=Path, default=default_db, help="Path to SQLite DB file.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    init_db(args.db)
    print(f"OK: initialized database at {args.db}")

