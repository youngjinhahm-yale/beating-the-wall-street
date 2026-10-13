"""Finds sharp intraday turning points in 1-minute index bars. Plain statistics, zero tokens."""

from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, time, timedelta

from . import config
from .db import rows

WINDOW = 15          # minutes per move
SIGMA_MULT = 3.0     # a move must exceed 3x the typical 15-minute move
MIN_MOVE_PCT = 0.30  # and be at least 0.30% so quiet days don't produce noise
MERGE_MIN = 30       # flags closer than this are one event
MAX_PER_DAY = 4
SKIP_UNTIL = time(9, 40)  # opening-auction swings in the first 10 minutes are noise, not news


def _returns(closes: list[float]) -> list[float]:
    return [(closes[i] / closes[i - WINDOW] - 1) * 100 for i in range(WINDOW, len(closes))]


def detect(con: sqlite3.Connection, ticker: str, day: str) -> list[dict]:
    """Inflection events for one ticker on one date (YYYY-MM-DD; market hours are the same UTC date)."""
    bars = rows(con.execute("SELECT ts, close FROM bars WHERE ticker = ? AND substr(ts, 1, 10) = ? ORDER BY ts",
                            (ticker, day)))
    if len(bars) < 60:
        return []
    history = rows(con.execute(
        "SELECT ts, close FROM bars WHERE ticker = ? AND substr(ts, 1, 10) < ? ORDER BY ts DESC LIMIT 7800",
        (ticker, day)))[::-1]
    base = _returns([b["close"] for b in history]) if len(history) > 200 else _returns([b["close"] for b in bars])
    sigma = statistics.pstdev(base) if len(base) > 10 else 0.1
    threshold = max(SIGMA_MULT * sigma, MIN_MOVE_PCT)

    closes = [b["close"] for b in bars]
    flags = []
    for i in range(WINDOW, len(closes)):
        if datetime.fromisoformat(bars[i - WINDOW]["ts"]).astimezone(config.ET).time() < SKIP_UNTIL:
            continue
        move = (closes[i] / closes[i - WINDOW] - 1) * 100
        if abs(move) >= threshold:
            flags.append({"start": bars[i - WINDOW]["ts"], "end": bars[i]["ts"], "move_pct": move})

    events: list[dict] = []
    for f in flags:
        t = datetime.fromisoformat(f["start"])
        if events and t - datetime.fromisoformat(events[-1]["start"]) < timedelta(minutes=MERGE_MIN):
            if abs(f["move_pct"]) > abs(events[-1]["move_pct"]):
                events[-1] = f  # keep the most extreme window of the cluster
            continue
        events.append(f)
    events.sort(key=lambda e: -abs(e["move_pct"]))
    out = []
    for e in sorted(events[:MAX_PER_DAY], key=lambda e: e["start"]):
        out.append({"ticker": ticker, "ts": e["start"], "end": e["end"],
                    "direction": "up" if e["move_pct"] > 0 else "down", "move_pct": round(e["move_pct"], 3),
                    "threshold": round(threshold, 3)})
    return out


def price_at(con: sqlite3.Connection, ticker: str, ts: str) -> float | None:
    r = con.execute("SELECT close FROM bars WHERE ticker = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
                    (ticker, ts)).fetchone()
    return r[0] if r else None


def intraday_chart(con: sqlite3.Connection, ticker: str, end_ts: str, minutes: int = 90, step: int = 3) -> list[dict]:
    start = (datetime.fromisoformat(end_ts) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    data = rows(con.execute("SELECT ts, close FROM bars WHERE ticker = ? AND ts >= ? AND ts <= ? ORDER BY ts",
                            (ticker, start, end_ts)))
    return [{"d": d["ts"], "c": round(d["close"], 2)} for d in data[::step]]
