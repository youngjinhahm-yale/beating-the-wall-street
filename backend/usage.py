"""Measured token usage and the cost model, straight from the llm_calls table."""

from __future__ import annotations

from . import config
from .db import connect, one, rows
from .llm import et_today

TRADING_DAYS_PER_MONTH = 21
FIXED_AGENTS = ("scout", "editor", "factcheck", "analyst", "coach")
PER_USER_AGENTS = ("coach_personal", "moderator")


def summary() -> dict:
    with connect() as con:
        total = one(con.execute(
            "SELECT COUNT(*) calls, COALESCE(SUM(input_tokens),0) inp, COALESCE(SUM(cache_read_tokens),0) cached, "
            "COALESCE(SUM(cache_write_tokens),0) cw, COALESCE(SUM(output_tokens),0) out, COALESCE(SUM(cost_usd),0) cost "
            "FROM llm_calls"))
        today = one(con.execute(
            "SELECT COUNT(*) calls, COALESCE(SUM(input_tokens + output_tokens),0) tokens, COALESCE(SUM(cost_usd),0) cost "
            "FROM llm_calls WHERE et_date = ?", (et_today(),)))
        by_agent = rows(con.execute(
            "SELECT agent, COUNT(*) calls, ROUND(AVG(requests),2) avg_requests, ROUND(AVG(input_tokens)) avg_input, "
            "ROUND(AVG(cache_read_tokens)) avg_cached, ROUND(AVG(output_tokens)) avg_output, "
            "AVG(cost_usd) avg_cost, SUM(cost_usd) total_cost, ROUND(AVG(latency_s),1) avg_latency "
            "FROM llm_calls WHERE status = 'ok' GROUP BY agent ORDER BY total_cost DESC"))
        by_run = rows(con.execute(
            "SELECT l.run_id, p.kind, p.status, MIN(l.ts) started, COUNT(*) calls, SUM(l.input_tokens) inp, "
            "SUM(l.cache_read_tokens) cached, SUM(l.output_tokens) out, SUM(l.cost_usd) cost "
            "FROM llm_calls l LEFT JOIN pipeline_runs p ON p.id = l.run_id WHERE l.run_id IS NOT NULL "
            "GROUP BY l.run_id ORDER BY started DESC LIMIT 30"))
        per_day = rows(con.execute(
            "SELECT et_date, COUNT(*) calls, SUM(input_tokens) inp, SUM(cache_read_tokens) cached, "
            "SUM(output_tokens) out, SUM(cost_usd) cost FROM llm_calls GROUP BY et_date ORDER BY et_date"))
        # One trading day = one morning pipeline + one evening pipeline. Average each over real runs.
        run_costs = rows(con.execute(
            "SELECT p.kind, l.run_id, SUM(l.cost_usd) cost, SUM(l.input_tokens + l.output_tokens) tokens "
            "FROM llm_calls l JOIN pipeline_runs p ON p.id = l.run_id WHERE p.status = 'ok' "
            "AND p.kind IN ('morning', 'manual', 'history', 'evening') GROUP BY l.run_id"))
        user_days = rows(con.execute(
            f"SELECT user_id, et_date, SUM(cost_usd) cost, SUM(input_tokens + output_tokens) tokens FROM llm_calls "
            f"WHERE status = 'ok' AND user_id IS NOT NULL AND agent IN ({','.join('?' * len(PER_USER_AGENTS))}) "
            f"GROUP BY user_id, et_date", PER_USER_AGENTS))
        active_user_days = con.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT user_id, substr(created_at,1,10) FROM bets)").fetchone()[0]

    p = config.PRICES.get(config.active_model_name(), config.PRICES["gpt-6-luna"])
    saved = total["cached"] * (p["input"] - p["cached"]) / 1_000_000
    def _avg(kinds, key):
        xs = [r[key] for r in run_costs if r["kind"] in kinds]
        return sum(xs) / len(xs) if xs else 0.0
    morning = ("morning", "manual", "history")
    fixed_per_day = _avg(morning, "cost") + _avg(("evening",), "cost")
    fixed_tokens_per_day = _avg(morning, "tokens") + _avg(("evening",), "tokens")
    n_morning = sum(1 for r in run_costs if r["kind"] in morning)
    n_evening = sum(1 for r in run_costs if r["kind"] == "evening")
    # Per active user-day: AI cost a player causes (coaching + moderation), spread over every day they play.
    per_user_day = (sum(d["cost"] for d in user_days) / max(active_user_days, len(user_days))) if user_days else 0.0
    per_user_month = per_user_day * TRADING_DAYS_PER_MONTH
    scale = []
    for n in (1, 100, 10_000):
        monthly = TRADING_DAYS_PER_MONTH * fixed_per_day + n * per_user_month
        scale.append({"users": n, "monthly_usd": round(monthly, 4), "per_user_month_usd": round(monthly / n, 6)})
    return {
        "model": config.active_model_name(), "prices_per_million": p,
        "totals": {**total, "cost": round(total["cost"], 6), "cache_savings_usd": round(saved, 6)},
        "today": {**today, "cost": round(today["cost"], 6), "cap_tokens": config.DAILY_TOKEN_CAP,
                  "user_cap_tokens": config.USER_DAILY_TOKEN_CAP},
        "by_agent": by_agent, "by_run": by_run, "per_day": per_day,
        "model_costs": {"fixed_per_trading_day_usd": round(fixed_per_day, 6),
                        "fixed_tokens_per_trading_day": round(fixed_tokens_per_day),
                        "morning_runs_measured": n_morning, "evening_runs_measured": n_evening,
                        "per_user_day_usd": round(per_user_day, 6), "per_user_month_usd": round(per_user_month, 6),
                        "trading_days_per_month": TRADING_DAYS_PER_MONTH, "scale": scale},
    }
