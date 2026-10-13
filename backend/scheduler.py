"""Background jobs (US Eastern time). Locking and settling are code-only and always on;
AI jobs run on schedule only when SCHEDULER=on (default in live mode)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import config, game, market, news, pipeline
from .db import connect, rows

log = logging.getLogger(__name__)


def scheduler_enabled() -> bool:
    default = "on" if config.DATA_MODE == "live" else "off"
    return os.getenv("SCHEDULER", default).strip().lower() in ("on", "1", "true", "yes")


def lock_and_settle() -> None:
    """Lock rounds at 09:25 ET; settle any locked round whose close is published (no tokens needed)."""
    now = datetime.now(timezone.utc)
    with connect() as con:
        game.lock_due_rounds(con, now)
        for r in rows(con.execute("SELECT id, trade_date FROM rounds WHERE status = 'locked'")):
            td = datetime.fromisoformat(r["trade_date"]).date()
            if now >= market.settle_dt(td).astimezone(timezone.utc):
                try:
                    res = game.settle_round(con, r["id"])
                    if res:
                        log.info("settled round %s", r["id"])
                except Exception as exc:
                    log.warning("settle %s failed: %s", r["id"], exc)


def poll_news() -> None:
    with connect() as con:
        n = news.refresh_live(con)
    log.info("news poll: %s new headlines", n)


async def morning_job() -> None:
    if market.is_trading_day(market.now_et().date()):
        try:
            await pipeline.run_morning(kind="morning")
        except Exception as exc:
            log.error("morning pipeline failed: %s", exc)


async def evening_job() -> None:
    if market.is_trading_day(market.now_et().date()):
        try:
            await pipeline.run_evening()
        except Exception as exc:
            log.error("evening pipeline failed: %s", exc)


def build() -> AsyncIOScheduler:
    s = AsyncIOScheduler(timezone=config.ET)
    s.add_job(lock_and_settle, "interval", minutes=2, id="lock_settle", next_run_time=datetime.now(config.ET))
    if scheduler_enabled():
        s.add_job(poll_news, "cron", day_of_week="mon-fri", hour="6-20", minute="*/20", id="news")
        s.add_job(morning_job, "cron", day_of_week="mon-fri", hour=6, minute=0, id="morning")
        s.add_job(evening_job, "cron", day_of_week="mon-fri", hour=16, minute=15, id="evening")
        s.add_job(evening_job, "cron", day_of_week="mon-fri", hour=18, minute=0, id="evening_retry")
    return s
