"""SQLite schema and small helpers. One file, one connection per call."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS policies (
  id TEXT PRIMARY KEY, name TEXT, insurer TEXT, sum_insured INTEGER,
  room_rent_cap INTEGER, copay_pct INTEGER, senior_copay_pct INTEGER, start_date TEXT
);
CREATE TABLE IF NOT EXISTS members (
  id TEXT PRIMARY KEY, name TEXT, phone TEXT, preferred_language TEXT, policy_id TEXT,
  area TEXT, pincode TEXT
);
CREATE TABLE IF NOT EXISTS insured_persons (
  id TEXT PRIMARY KEY, member_id TEXT, name TEXT, relation TEXT, age INTEGER
);
CREATE TABLE IF NOT EXISTS procedures (
  code TEXT PRIMARY KEY, name_en TEXT, name_hi TEXT, aliases TEXT, specialty TEXT,
  cost_low INTEGER, cost_high INTEGER, stay_days INTEGER
);
CREATE TABLE IF NOT EXISTS coverage_rules (
  policy_id TEXT, procedure_code TEXT, covered INTEGER, waiting_period_months INTEGER,
  sub_limit INTEGER, note TEXT, PRIMARY KEY (policy_id, procedure_code)
);
CREATE TABLE IF NOT EXISTS hospitals (
  id TEXT PRIMARY KEY, name TEXT, area TEXT, pincode TEXT, address TEXT, lat REAL, lng REAL,
  specialties TEXT, network INTEGER, cashless INTEGER, tier INTEGER
);
CREATE TABLE IF NOT EXISTS slots (
  id INTEGER PRIMARY KEY AUTOINCREMENT, hospital_id TEXT, start_at TEXT, booked INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bookings (
  id TEXT PRIMARY KEY, member_id TEXT, insured_person_id TEXT, hospital_id TEXT, slot_id INTEGER,
  start_at TEXT, calendar_link TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY, member_id TEXT, insured_person_id TEXT, hospital_id TEXT, procedure_code TEXT,
  type TEXT, status TEXT, pending_docs TEXT, amount INTEGER, admission_date TEXT,
  note TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, claim_id TEXT, path TEXT, mime TEXT, doc_type TEXT, fields TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS reminders (
  id TEXT PRIMARY KEY, member_id TEXT, due_at TEXT, text TEXT, sent INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY, member_id TEXT, claim_id TEXT, reason TEXT, callback_at TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS memories (
  member_id TEXT, key TEXT, value TEXT, updated_at TEXT, PRIMARY KEY (member_id, key)
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, member_id TEXT, channel TEXT, agent_id TEXT, mode TEXT, reason TEXT,
  started_at TEXT, ended_at TEXT, summary_sent INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, member_id TEXT, type TEXT, payload TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, member_id TEXT, channel TEXT, text TEXT, delivered INTEGER, ts TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

JSON_COLS = {"aliases", "specialties", "pending_docs", "fields"}


def now() -> datetime:
    return datetime.now(config.IST)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with tx() as c:
        c.executescript(SCHEMA)


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except json.JSONDecodeError:
                pass
    return d


def one(sql: str, *args: Any) -> dict[str, Any] | None:
    with tx() as c:
        return _row(c.execute(sql, args).fetchone())


def all_(sql: str, *args: Any) -> list[dict[str, Any]]:
    with tx() as c:
        return [_row(r) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]


def run(sql: str, *args: Any) -> int:
    with tx() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid or cur.rowcount


def next_id(prefix: str, table: str, start: int = 2001) -> str:
    rows = all_(f"SELECT id FROM {table} WHERE id LIKE ?", f"{prefix}-%")
    nums = [int(r["id"].split("-")[-1]) for r in rows if r["id"].split("-")[-1].isdigit()]
    return f"{prefix}-{max(nums, default=start - 1) + 1}"


# ---- settings / flags ----

def flag(name: str) -> bool:
    r = one("SELECT value FROM settings WHERE key = ?", name)
    if r is None:
        return config.DEFAULT_FLAGS.get(name, False)
    return r["value"] == "1"


def set_flag(name: str, on: bool) -> None:
    run("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        name, "1" if on else "0")


def flags() -> dict[str, bool]:
    return {n: flag(n) for n in config.FLAG_NAMES}
