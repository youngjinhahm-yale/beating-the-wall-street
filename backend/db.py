"""SQLite schema and a tiny connection helper. One file holds the whole game."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pin_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    points INTEGER NOT NULL,
    streak INTEGER NOT NULL DEFAULT 0,
    last_play_date TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_bot INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS category_ratings (
    user_id INTEGER NOT NULL REFERENCES users(id),
    category TEXT NOT NULL,
    lp REAL NOT NULL,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    bets INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, category)
);
CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY,
    trade_date TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('open', 'locked', 'settled')),
    lock_at TEXT NOT NULL,
    brief TEXT,
    snapshot TEXT,
    created_at TEXT NOT NULL,
    settled_at TEXT
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    round_id INTEGER REFERENCES rounds(id),
    kind TEXT NOT NULL CHECK (kind IN ('daily', 'inflection')),
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    context TEXT,
    ticker TEXT NOT NULL,
    ticker_name TEXT,
    question TEXT NOT NULL,
    event_time TEXT,
    window_start TEXT,
    window_end TEXT,
    flat_band REAL NOT NULL,
    base_rates TEXT NOT NULL,
    ref_price REAL,
    close_price REAL,
    move_pct REAL,
    outcome TEXT CHECK (outcome IN ('up', 'flat', 'down', 'void')),
    sources TEXT,
    post_mortem TEXT,
    lesson_rule TEXT,
    chart TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    card_id INTEGER NOT NULL REFERENCES cards(id),
    direction TEXT NOT NULL CHECK (direction IN ('up', 'flat', 'down')),
    stake INTEGER NOT NULL,
    reason TEXT NOT NULL,
    rule_id INTEGER REFERENCES rules(id),
    practice INTEGER NOT NULL DEFAULT 0,
    crowd_share REAL,
    payout INTEGER,
    lp_change REAL,
    coach_note TEXT,
    created_at TEXT NOT NULL,
    settled_at TEXT,
    UNIQUE (user_id, card_id)
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    text TEXT NOT NULL,
    category TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    uses INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bars (
    ticker TEXT NOT NULL,
    ts TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (ticker, ts)
);
CREATE TABLE IF NOT EXISTS daily_prices (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS headlines (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    url TEXT NOT NULL UNIQUE,
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_headlines_time ON headlines(published_at);
CREATE TABLE IF NOT EXISTS inflections (
    id INTEGER PRIMARY KEY,
    ticker TEXT NOT NULL,
    ts TEXT NOT NULL,
    direction TEXT NOT NULL,
    move_pct REAL NOT NULL,
    headline_id INTEGER REFERENCES headlines(id),
    cause TEXT,
    mechanism TEXT,
    confidence TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (ticker, ts)
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    card_id INTEGER REFERENCES cards(id),
    category TEXT,
    parent_id INTEGER REFERENCES posts(id),
    title TEXT,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('visible', 'rejected', 'flagged', 'hidden')),
    mod_reason TEXT,
    mod_by TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
    post_id INTEGER NOT NULL REFERENCES posts(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    value INTEGER NOT NULL CHECK (value IN (-1, 1)),
    weight REAL NOT NULL DEFAULT 1,
    PRIMARY KEY (post_id, user_id)
);
CREATE TABLE IF NOT EXISTS reports (
    post_id INTEGER NOT NULL REFERENCES posts(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    PRIMARY KEY (post_id, user_id)
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    et_date TEXT NOT NULL,
    agent TEXT NOT NULL,
    run_id TEXT,
    user_id INTEGER,
    model TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_s REAL,
    status TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    round_id INTEGER,
    trace TEXT
);
CREATE TABLE IF NOT EXISTS missions (
    user_id INTEGER NOT NULL REFERENCES users(id),
    round_id INTEGER NOT NULL REFERENCES rounds(id),
    kind TEXT NOT NULL,
    bonus INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, round_id, kind)
);
CREATE TABLE IF NOT EXISTS safety_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT,
    user_id INTEGER
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(path: Path | None = None) -> None:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(Path(path or config.DB_PATH), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def one(cur: sqlite3.Cursor) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r else None


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(text: str | None, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def log_safety(con: sqlite3.Connection, kind: str, detail: str, user_id: int | None = None) -> None:
    con.execute("INSERT INTO safety_events (ts, kind, detail, user_id) VALUES (?, ?, ?, ?)",
                (utcnow(), kind, detail[:2000], user_id))
