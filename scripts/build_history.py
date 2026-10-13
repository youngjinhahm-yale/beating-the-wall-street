"""Replay the real pipelines over past trading days to build history (Replay cards, lessons, inflections).

For each past day D it uses only information available at the time:
  * headlines from Google News for D-1 and D (stored with their real publish times),
  * the morning pipeline "as of" 06:00 ET on D (agents can only search news before that time),
  * settlement from D's real open and close,
  * the evening pipeline on D's real 1-minute bars.

    python scripts/build_history.py --start 2026-09-28 --end 2026-10-12
"""

from __future__ import annotations

import sys as _sys

for _s in (_sys.stdout, _sys.stderr):  # Windows consoles may not be UTF-8
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import argparse
import asyncio
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, game, market, news, pipeline  # noqa: E402
from backend.agents import close_hub  # noqa: E402
from backend.db import connect, init_db, one  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    init_db()
    print(f"DB: {config.DB_PATH}")

    with connect() as con:
        for t in market.BAR_TICKERS:
            n = market.store_bars(con, t, market.fetch_minute_bars_range(t, start, end))
            print(f"bars {t}: {n}")

    d = start if market.is_trading_day(start) else market.next_trading_day(start)
    try:
        while d <= end:
            print(f"\n=== {d} ===")
            prev = market.prev_trading_day(d)
            with connect() as con:
                added = news.refresh_google(con, after=prev.isoformat(), before=(d + timedelta(days=1)).isoformat())
            print(f"headlines added: {added}")
            as_of = datetime.combine(d, time(6, 0), tzinfo=config.ET)
            try:
                r = await pipeline.run_morning(as_of, kind="history")
                print("morning:", r["status"], [c["title"] for c in r.get("cards", [])])
            except Exception as exc:
                print("morning failed:", exc)
            with connect() as con:
                rnd = one(con.execute("SELECT id FROM rounds WHERE trade_date = ?", (d.isoformat(),)))
                if rnd:
                    con.execute("UPDATE rounds SET status = 'locked' WHERE id = ? AND status = 'open'", (rnd["id"],))
                    res = game.settle_round(con, rnd["id"])
                    print("settled:", res and [(c["outcome"], round(c["move_pct"] or 0, 2)) for c in res["cards"]])
            try:
                r = await pipeline.run_evening(d, live=False, max_inflections=2)
                print("evening:", r["inflections"], "inflections,", r["post_mortems"], "post-mortems")
            except Exception as exc:
                print("evening failed:", exc)
            d = market.next_trading_day(d)
    finally:
        await close_hub()


if __name__ == "__main__":
    asyncio.run(main())
