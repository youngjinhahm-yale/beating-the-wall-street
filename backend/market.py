"""Free market data (Yahoo Finance chart API), the trading calendar, Flat bands and base rates.

Everything numeric in the game comes from here or from the database, never from a model.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from . import config
from .db import dumps, rows

log = logging.getLogger(__name__)

INDEX_NAMES = {
    "^GSPC": "S&P 500", "^IXIC": "Nasdaq Composite", "^DJI": "Dow Jones", "^RUT": "Russell 2000",
}
# Targets the Editor may pick without extra checks. Single stocks are allowed too
# once validate_ticker() finds real daily prices for them.
SUGGESTED_TARGETS = {
    **INDEX_NAMES,
    "XLE": "Energy sector ETF", "XLF": "Financials sector ETF", "XLK": "Tech sector ETF",
    "SMH": "Semiconductor ETF", "XLV": "Health care ETF", "XLI": "Industrials ETF",
    "ITA": "Aerospace & defense ETF", "XLY": "Consumer discretionary ETF", "XLP": "Consumer staples ETF",
    "XLU": "Utilities ETF", "KRE": "Regional banks ETF", "GLD": "Gold ETF", "USO": "Oil ETF",
    "TLT": "20+ year Treasury bond ETF", "UUP": "U.S. dollar ETF",
}
SNAPSHOT_TICKERS = {
    "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow", "ES=F": "S&P futures", "NQ=F": "Nasdaq futures",
    "^VIX": "VIX", "^TNX": "10Y yield", "DX-Y.NYB": "Dollar index", "CL=F": "Crude oil", "GC=F": "Gold",
}
BAR_TICKERS = ["^GSPC", "^IXIC", "^DJI"]


# --- calendar ------------------------------------------------------------------------

def now_et() -> datetime:
    return datetime.now(config.ET)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in config.MARKET_HOLIDAYS


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def lock_dt(trade_date: date) -> datetime:
    return datetime.combine(trade_date, time(*config.LOCK_TIME), tzinfo=config.ET)


def settle_dt(trade_date: date) -> datetime:
    return datetime.combine(trade_date, time(*config.SETTLE_TIME), tzinfo=config.ET)


def target_trade_date(now: datetime | None = None) -> date:
    """The session a newly built round bets on: today if betting is still open, else the next one."""
    now = now or now_et()
    today = now.date()
    if is_trading_day(today) and now < lock_dt(today):
        return today
    return next_trading_day(today)


# --- Yahoo Finance (plain JSON) -------------------------------------------------------------

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (BeatingTheWallStreet educational app)"}


def _chart(ticker: str, interval: str, start: datetime, end: datetime) -> dict | None:
    """Yahoo Finance's public chart endpoint (no key). Plain requests + JSON: no numpy/pandas needed."""
    import requests
    params = {"interval": interval, "period1": int(start.timestamp()), "period2": int(end.timestamp()),
              "includePrePost": "false"}
    r = requests.get(YAHOO_CHART.format(ticker=requests.utils.quote(ticker, safe="")), params=params,
                     headers=_HEADERS, timeout=20)
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result") or []
    return res[0] if res else None


def _rows(res: dict | None) -> list[tuple[int, int, float, float, float, float, float]]:
    """(timestamp, gmt offset, open, high, low, close, volume), skipping empty bars."""
    if not res or not res.get("timestamp"):
        return []
    q = res["indicators"]["quote"][0]
    off = int(res.get("meta", {}).get("gmtoffset") or 0)
    out = []
    for i, t in enumerate(res["timestamp"]):
        o, h, lo, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, lo, c):
            continue
        out.append((t, off, float(o), float(h), float(lo), float(c), float((q.get("volume") or [0])[i] or 0)))
    return out


def fetch_daily(ticker: str, days: int = 120) -> list[dict]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(days * 1.5) + 5)  # calendar days covering `days` sessions
    out = []
    for t, off, o, h, lo, c, _ in _rows(_chart(ticker, "1d", start, end)):
        # The session date in the exchange's own time zone.
        d = datetime.fromtimestamp(t + off, timezone.utc).date()
        out.append({"date": d.isoformat(), "open": o, "high": h, "low": lo, "close": c})
    return out[-days:]


def _bars(res: dict | None) -> list[dict]:
    return [{"ts": datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds"), "open": o, "high": h,
             "low": lo, "close": c, "volume": v} for t, _, o, h, lo, c, v in _rows(res)]


def fetch_minute_bars(ticker: str, period: str = "5d") -> list[dict]:
    days = int(period.rstrip("d") or 5)
    end = datetime.now(timezone.utc)
    return _bars(_chart(ticker, "1m", end - timedelta(days=days), end))


def fetch_minute_bars_range(ticker: str, start: date, end: date) -> list[dict]:
    """1-minute bars between two dates (Yahoo keeps ~30 days), fetched in 7-day chunks."""
    out: list[dict] = []
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=7), end + timedelta(days=1))
        a = datetime.combine(cur, time(0), tzinfo=timezone.utc)
        b = datetime.combine(stop, time(0), tzinfo=timezone.utc)
        out += _bars(_chart(ticker, "1m", a, b))
        cur = stop
    return out


def store_daily(con: sqlite3.Connection, ticker: str, data: list[dict]) -> None:
    con.executemany(
        "INSERT OR REPLACE INTO daily_prices (ticker, date, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
        [(ticker, d["date"], d["open"], d["high"], d["low"], d["close"]) for d in data])


def store_bars(con: sqlite3.Connection, ticker: str, data: list[dict]) -> int:
    con.executemany(
        "INSERT OR IGNORE INTO bars (ticker, ts, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(ticker, b["ts"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in data])
    return len(data)


def refresh_daily(con: sqlite3.Connection, ticker: str, days: int = 120) -> list[dict]:
    """Fetch daily prices if we can, then return whatever the database has."""
    if config.DATA_MODE == "live" or not _has_daily(con, ticker):
        try:
            store_daily(con, ticker, fetch_daily(ticker, days))
        except Exception as exc:  # network down: fall back to stored data
            log.warning("daily fetch failed for %s: %s", ticker, exc)
    return rows(con.execute(
        "SELECT * FROM daily_prices WHERE ticker = ? ORDER BY date DESC LIMIT ?", (ticker, days)))[::-1]


def _has_daily(con: sqlite3.Connection, ticker: str) -> bool:
    return con.execute("SELECT 1 FROM daily_prices WHERE ticker = ? LIMIT 1", (ticker,)).fetchone() is not None


def validate_ticker(con: sqlite3.Connection, ticker: str) -> bool:
    ticker = ticker.strip().upper()
    if not ticker or len(ticker) > 12 or "=" in ticker:  # futures trade overnight; not a clean open/close
        return False
    return len(refresh_daily(con, ticker, 60)) >= 25


# --- Flat band and base rates --------------------------------------------------------

def flat_band_and_base_rates(daily: list[dict], before: str | None = None) -> tuple[float, dict]:
    """Flat band (in %) from the 20 sessions before `before`, and Up/Flat/Down base rates over 60."""
    hist = [d for d in daily if before is None or d["date"] < before]
    moves = [(d["close"] / d["open"] - 1) * 100 for d in hist if d["open"]]
    recent = moves[-20:]
    if not recent:
        return 0.2, {"up": 0.4, "flat": 0.2, "down": 0.4}
    band = round(max(0.1, config.FLAT_BAND_FACTOR * sum(abs(m) for m in recent) / len(recent)), 2)
    sample = moves[-60:]
    counts = {"up": 0, "flat": 0, "down": 0}
    for m in sample:
        counts[classify(m, band)] += 1
    n = len(sample)
    # Light smoothing so no outcome ever has a zero base rate.
    rates = {k: round((v + 1) / (n + 3), 3) for k, v in counts.items()}
    return band, rates


def classify(move_pct: float, band: float) -> str:
    if move_pct > band:
        return "up"
    if move_pct < -band:
        return "down"
    return "flat"


def mini_chart(daily: list[dict], before: str | None = None, n: int = 30) -> list[dict]:
    hist = [d for d in daily if before is None or d["date"] < before][-n:]
    return [{"d": d["date"], "c": round(d["close"], 2)} for d in hist]


def ticker_name(ticker: str) -> str:
    return SUGGESTED_TARGETS.get(ticker, ticker)


# --- snapshot for cards and agents ---------------------------------------------------

def snapshot(con: sqlite3.Connection) -> list[dict]:
    """Latest close and 1-day change for the headline indicators (live mode fetches fresh)."""
    out = []
    for t, name in SNAPSHOT_TICKERS.items():
        data = refresh_daily(con, t, 10)
        if len(data) >= 2:
            last, prev = data[-1], data[-2]
            out.append({"ticker": t, "name": name, "date": last["date"], "close": round(last["close"], 2),
                        "change_pct": round((last["close"] / prev["close"] - 1) * 100, 2)})
    return out


def snapshot_json(con: sqlite3.Connection) -> str:
    return dumps(snapshot(con))


# --- live tracker (during the session) -----------------------------------------------

_live_cache: dict[str, tuple[float, dict]] = {}


def live_move(ticker: str, trade_date: str, ttl: int = 60) -> dict | None:
    """Today's open and latest price for a ticker, cached for `ttl` seconds. None before the open."""
    import time as _t
    hit = _live_cache.get(ticker)
    if hit and _t.time() - hit[0] < ttl:
        return hit[1]
    try:
        start = datetime.combine(date.fromisoformat(trade_date), time(9, 30), tzinfo=config.ET)
        bars = _bars(_chart(ticker, "1m", start, start + timedelta(hours=7)))
    except Exception as exc:
        log.warning("live fetch failed for %s: %s", ticker, exc)
        return hit[1] if hit else None
    if not bars:
        return None
    o, last = bars[0]["open"], bars[-1]["close"]
    data = {"open": round(o, 2), "last": round(last, 2), "move_pct": round((last / o - 1) * 100, 3),
            "as_of": bars[-1]["ts"]}
    _live_cache[ticker] = (_t.time(), data)
    return data
