"""Free market data (yfinance), the trading calendar, Flat bands and base rates.

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


# --- yfinance wrappers ---------------------------------------------------------------

def _yf():
    import yfinance as yf  # imported lazily: slow import, and sample mode may never need it
    return yf


def fetch_daily(ticker: str, days: int = 120) -> list[dict]:
    df = _yf().download(ticker, period=f"{days}d", interval="1d", progress=False, auto_adjust=False)
    if df is None or df.empty:
        return []
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    out = []
    for idx, r in df.iterrows():
        if any(v != v for v in (r["Open"], r["Close"])):  # NaN
            continue
        out.append({"date": idx.date().isoformat(), "open": float(r["Open"]), "high": float(r["High"]),
                    "low": float(r["Low"]), "close": float(r["Close"])})
    return out


def fetch_minute_bars(ticker: str, period: str = "5d") -> list[dict]:
    df = _yf().download(ticker, period=period, interval="1m", progress=False, auto_adjust=False)
    if df is None or df.empty:
        return []
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    out = []
    for idx, r in df.iterrows():
        if r["Close"] != r["Close"]:
            continue
        ts = idx.to_pydatetime().astimezone(timezone.utc).isoformat(timespec="seconds")
        out.append({"ts": ts, "open": float(r["Open"]), "high": float(r["High"]), "low": float(r["Low"]),
                    "close": float(r["Close"]), "volume": float(r.get("Volume", 0) or 0)})
    return out


def fetch_minute_bars_range(ticker: str, start: date, end: date) -> list[dict]:
    """1-minute bars between two dates (Yahoo keeps ~30 days), fetched in 7-day chunks."""
    out: list[dict] = []
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=7), end + timedelta(days=1))
        df = _yf().download(ticker, start=cur.isoformat(), end=stop.isoformat(), interval="1m", progress=False,
                            auto_adjust=False)
        if df is not None and not df.empty:
            if hasattr(df.columns, "levels"):
                df.columns = df.columns.get_level_values(0)
            for idx, r in df.iterrows():
                if r["Close"] != r["Close"]:
                    continue
                out.append({"ts": idx.to_pydatetime().astimezone(timezone.utc).isoformat(timespec="seconds"),
                            "open": float(r["Open"]), "high": float(r["High"]), "low": float(r["Low"]),
                            "close": float(r["Close"]), "volume": float(r.get("Volume", 0) or 0)})
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
