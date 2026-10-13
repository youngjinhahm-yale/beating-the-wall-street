"""Runs one agent with a budget check before and a usage record after.

Every model call in the app goes through run_agent(), so llm_calls is a complete,
measured record of tokens and dollars (the evidence for the tokenomics section).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import RunUsage, UsageLimits

from . import config
from .db import connect, utcnow

log = logging.getLogger(__name__)

LIMITS = UsageLimits(
    request_limit=config.AGENT_REQUEST_LIMIT,
    tool_calls_limit=config.AGENT_TOOL_CALL_LIMIT,
    total_tokens_limit=config.AGENT_TOTAL_TOKEN_LIMIT,
)


class ContentBlocked(RuntimeError):
    """The model provider's own content filter refused the request (an extra safety layer)."""


class BudgetExceeded(RuntimeError):
    """The daily token cap (global or per user) is used up; AI features pause, the game keeps running."""


def et_today() -> str:
    return datetime.now(config.ET).date().isoformat()


def tokens_used_today(user_id: int | None = None) -> int:
    with connect() as con:
        if user_id is None:
            q = con.execute("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM llm_calls WHERE et_date = ?",
                            (et_today(),))
        else:
            q = con.execute("SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM llm_calls "
                            "WHERE et_date = ? AND user_id = ?", (et_today(), user_id))
        return int(q.fetchone()[0])


def check_budget(user_id: int | None = None) -> None:
    used = tokens_used_today()
    if used >= config.DAILY_TOKEN_CAP:
        _record_block(f"daily cap {config.DAILY_TOKEN_CAP:,} reached ({used:,} used)", user_id)
        raise BudgetExceeded(f"Today's AI budget ({config.DAILY_TOKEN_CAP:,} tokens) is used up. "
                             "The game still works; AI features return tomorrow.")
    if user_id is not None:
        mine = tokens_used_today(user_id)
        if mine >= config.USER_DAILY_TOKEN_CAP:
            _record_block(f"user cap {config.USER_DAILY_TOKEN_CAP:,} reached ({mine:,} used)", user_id)
            raise BudgetExceeded(f"You've used today's personal AI budget ({config.USER_DAILY_TOKEN_CAP:,} tokens). "
                                 "Try again tomorrow.")


def _record_block(detail: str, user_id: int | None) -> None:
    with connect() as con:
        con.execute("INSERT INTO safety_events (ts, kind, detail, user_id) VALUES (?, 'budget_block', ?, ?)",
                    (utcnow(), detail, user_id))


def cost_usd(model: str, inp: int, cache_read: int, cache_write: int, out: int) -> float:
    p = config.PRICES.get(model, config.PRICES["gpt-6-luna"])
    uncached = max(0, inp - cache_read - cache_write)
    return (uncached * p["input"] + cache_read * p["cached"] + cache_write * p["cache_write"]
            + out * p["output"]) / 1_000_000


def _log(agent: str, run_id: str | None, user_id: int | None, usage: Any, latency: float, status: str,
         note: str = "") -> dict:
    model = config.active_model_name()
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    cr = int(getattr(usage, "cache_read_tokens", 0) or 0)
    cw = int(getattr(usage, "cache_write_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    req = int(getattr(usage, "requests", 0) or 0)
    cost = cost_usd(model, inp, cr, cw, out)
    with connect() as con:
        con.execute(
            "INSERT INTO llm_calls (ts, et_date, agent, run_id, user_id, model, requests, input_tokens, "
            "cache_read_tokens, cache_write_tokens, output_tokens, cost_usd, latency_s, status, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (utcnow(), et_today(), agent, run_id, user_id, model, req, inp, cr, cw, out, cost,
             round(latency, 2), status, note[:500]))
    return {"agent": agent, "requests": req, "input_tokens": inp, "cache_read_tokens": cr,
            "cache_write_tokens": cw, "output_tokens": out, "cost_usd": round(cost, 6),
            "latency_s": round(latency, 2), "status": status}


async def run_agent(agent: Agent, name: str, prompt: str, *, run_id: str | None = None,
                    user_id: int | None = None) -> tuple[Any, dict]:
    """Run an agent once. Returns (output, usage_record). Raises BudgetExceeded before spending anything."""
    check_budget(user_id)
    started = time.perf_counter()
    usage = RunUsage()  # shared with the run so tokens are logged even if it is stopped
    try:
        result = await agent.run(prompt, usage=usage, usage_limits=LIMITS)
    except UsageLimitExceeded as exc:
        _log(name, run_id, user_id, usage, time.perf_counter() - started, "stopped_limit", str(exc))
        with connect() as con:
            con.execute("INSERT INTO safety_events (ts, kind, detail, user_id) VALUES (?, 'agent_limit', ?, ?)",
                        (utcnow(), f"{name}: {exc}", user_id))
        raise
    except Exception as exc:
        blocked = "content_filter" in repr(exc)
        _log(name, run_id, user_id, usage, time.perf_counter() - started, "blocked" if blocked else "error", repr(exc))
        if blocked:
            with connect() as con:
                con.execute("INSERT INTO safety_events (ts, kind, detail, user_id) VALUES (?, 'provider_content_filter', ?, ?)",
                            (utcnow(), f"{name}: request refused by the model provider's content filter", user_id))
            raise ContentBlocked(f"{name}: the AI provider's safety filter refused this request.") from exc
        raise
    rec = _log(name, run_id, user_id, usage, time.perf_counter() - started, "ok")
    return result.output, rec


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
