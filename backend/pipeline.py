"""Deterministic orchestration. Python decides what runs when; agents only read, write and judge text.

Morning:  news -> pre-filter (code) -> Scout -> Editor -> direction check (code) + Fact-checker
          -> (one Editor retry for failed cards) -> publish round.
Evening:  lock + settle (code) -> minute bars (code) -> inflection scan (code) -> Inflection Analyst
          -> Coach post-mortems.
Every step is written to pipeline_runs.trace so one real run can be shown step by step.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from . import config, game, inflection, market, news, safety
from .agents import hub
from .agents import models as m
from .db import connect, dumps, loads, one, rows, utcnow
from .llm import run_agent

log = logging.getLogger(__name__)
_run_lock = asyncio.Lock()


class PipelineError(RuntimeError):
    pass


class Trace:
    def __init__(self, kind: str) -> None:
        self.id = f"{kind}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"
        self.kind = kind
        self.steps: list[dict] = []
        with connect() as con:
            con.execute("INSERT INTO pipeline_runs (id, kind, started_at, status) VALUES (?, ?, ?, 'running')",
                        (self.id, kind, utcnow()))

    def add(self, step: str, detail: dict, usage: dict | None = None) -> None:
        self.steps.append({"step": step, "at": utcnow(), "detail": detail, "usage": usage})
        self._save("running")

    def finish(self, status: str, round_id: int | None = None) -> None:
        self._save(status, round_id, done=True)

    def _save(self, status: str, round_id: int | None = None, done: bool = False) -> None:
        with connect() as con:
            con.execute("UPDATE pipeline_runs SET status = ?, trace = ?, round_id = COALESCE(?, round_id), "
                        "finished_at = CASE WHEN ? THEN ? ELSE finished_at END WHERE id = ?",
                        (status, dumps(self.steps), round_id, done, utcnow(), self.id))


def _snapshot_before(con, trade_date: str, live: bool) -> list[dict]:
    out = []
    for t, name in market.SNAPSHOT_TICKERS.items():
        data = market.refresh_daily(con, t, 15)
        cut = [d for d in data if d["date"] < trade_date or (live and "=" in t and d["date"] <= trade_date)]
        if len(cut) >= 2:
            last, prev = cut[-1], cut[-2]
            out.append({"name": name, "ticker": t, "as_of": last["date"], "close": round(last["close"], 2),
                        "change_pct": round((last["close"] / prev["close"] - 1) * 100, 2)})
    return out


def _mentions_date(title: str, d: date) -> bool:
    """True if a headline names the trade date (e.g. 'Sept. 28'), a sign it was written after the session started."""
    t = title.lower()
    months = {d.strftime("%B").lower(), d.strftime("%b").lower(), d.strftime("%b").lower() + ".", "sept", "sept."}
    if d.month != 9:
        months -= {"sept", "sept."}
    return any(f"{m} {d.day}" in t or f"{m} {d.day:02d}" in t for m in months)


def _fallback_ticker(category: str) -> str:
    return "^IXIC" if category == "company" else "^GSPC"


# --- morning -------------------------------------------------------------------------

async def run_morning(as_of: datetime | None = None, *, force: bool = False, kind: str = "morning",
                      prefilter: bool = True) -> dict:
    async with _run_lock:
        return await _morning(as_of, force=force, kind=kind, prefilter=prefilter)


async def _morning(as_of, *, force, kind, prefilter) -> dict:
    live = as_of is None
    as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    trade_date = market.target_trade_date(as_of.astimezone(config.ET))
    td = trade_date.isoformat()
    with connect() as con:
        existing = one(con.execute("SELECT id FROM rounds WHERE trade_date = ?", (td,)))
    if existing and not force:
        return {"status": "exists", "round_id": existing["id"], "trade_date": td}

    trace = Trace(kind)
    try:
        h = await hub()
        # 1. News in (code).
        with connect() as con:
            fetched = news.refresh_live(con) if live else 0
            start_et = datetime.combine(market.prev_trading_day(trade_date), datetime.min.time(),
                                        tzinfo=config.ET).replace(hour=9)
            start = start_et.astimezone(timezone.utc).isoformat(timespec="seconds")
            end_dt = as_of
            if not live:
                # History: many archive headlines carry a date-only stamp (07:00 UTC), so anything stamped on the
                # trade date itself may really be from later that day. Cut the window at midnight ET to avoid leaks.
                end_dt = min(as_of, datetime.combine(trade_date, datetime.min.time(), tzinfo=config.ET).astimezone(timezone.utc))
            end = end_dt.isoformat(timespec="seconds")
            total = con.execute("SELECT COUNT(*) FROM headlines WHERE published_at >= ? AND published_at < ?",
                                (start, end)).fetchone()[0]
            cands = [c for c in news.candidates(con, start, end, limit=60, prefilter=prefilter)
                     if not _mentions_date(c["title"], trade_date)][:40 if prefilter else 300]
            snap = _snapshot_before(con, td, live)
        trace.add("news_prefilter", {"trade_date": td, "window": [start, end], "new_rss_items": fetched,
                                     "headlines_in_window": total, "candidates_after_prefilter": len(cands),
                                     "prefilter": prefilter})
        if len(cands) < 3:
            raise PipelineError(f"Only {len(cands)} usable headlines in the window; need at least 3.")
        by_id = {c["id"]: c for c in cands}

        # 2. Scout.
        scout_prompt = (
            f"Round for trade date {td} (bets settle on the open-to-close move).\n"
            f"Current time (UTC): {end}. When calling search_news use end_iso=\"{end}\".\n"
            f"Market snapshot (latest closes before the round): {dumps(snap)}\n\n"
            + safety.quote_untrusted("HEADLINES", cands, ["id", "source", "published_at", "title", "summary"]))
        scout, u = await run_agent(h.agents["scout"], "scout", scout_prompt, run_id=trace.id)
        trace.add("scout", {"events": [e.model_dump() for e in scout.events],
                            "market_backdrop": scout.market_backdrop}, u)
        if not scout.events:
            raise PipelineError("Scout found no market-moving events.")

        # 3. Editor -> checks -> one retry.
        cited_ids = {i for e in scout.events for i in e.headline_ids}
        cited = [by_id[i] for i in cited_ids if i in by_id]
        editor_prompt = (
            f"Trade date {td}. Scout report:\n{scout.model_dump_json()}\n\n"
            + safety.quote_untrusted("HEADLINES", cited, ["id", "source", "published_at", "title", "summary"]))
        draft, u = await run_agent(h.agents["editor"], "editor", editor_prompt, run_id=trace.id)
        trace.add("editor", {"brief": draft.brief, "cards": [c.model_dump() for c in draft.cards]}, u)

        good, failed = await _check_cards(h, trace, draft.cards, by_id)
        if failed and len(good) < 3:
            fix_prompt = (
                editor_prompt + "\n\nThese draft cards failed review. Rewrite each one so every claim is "
                "supported by its headlines and nothing hints at direction. Return only the rewritten cards.\n"
                + dumps([{"card": c.model_dump(), "problems": p} for c, p in failed]))
            redo, u = await run_agent(h.agents["editor"], "editor", fix_prompt, run_id=trace.id)
            trace.add("editor_retry", {"cards": [c.model_dump() for c in redo.cards]}, u)
            good2, failed2 = await _check_cards(h, trace, redo.cards, by_id, step="factcheck_retry")
            good += good2
        if not good:
            raise PipelineError("No card passed the fact-check.")

        # 4. Publish (code): tickers, Flat bands, base rates, charts.
        with connect() as con:
            if existing and force:
                _delete_round(con, existing["id"])
            cur = con.execute("INSERT INTO rounds (trade_date, status, lock_at, brief, snapshot, created_at) "
                              "VALUES (?, 'open', ?, ?, ?, ?)",
                              (td, market.lock_dt(trade_date).astimezone(timezone.utc).isoformat(timespec="seconds"),
                               draft.brief, dumps(snap), utcnow()))
            round_id = cur.lastrowid
            used: set[str] = set()
            published = []
            for c in good[:5]:
                ticker = c.ticker.strip().upper()
                if ticker in used or not market.validate_ticker(con, ticker):
                    ticker = _fallback_ticker(c.category)
                    if ticker in used:
                        ticker = next(t for t in ("^GSPC", "^IXIC", "^DJI", "^RUT") if t not in used)
                used.add(ticker)
                band, rates, chart, ctx = game.context_for_card(con, ticker, td)
                name = market.ticker_name(ticker)
                sources = [{"title": by_id[i]["title"], "url": by_id[i]["url"], "source": by_id[i]["source"],
                            "published_at": by_id[i]["published_at"]} for i in c.headline_ids if i in by_id]
                label = ticker if name == ticker else f"{name} ({ticker})"
                question = (f"Will {label} close Up, Flat (within ±{band}%), or Down "
                            f"versus its opening price on {td}?")
                cur = con.execute(
                    "INSERT INTO cards (round_id, kind, category, title, summary, context, ticker, ticker_name, "
                    "question, flat_band, base_rates, sources, chart, created_at) "
                    "VALUES (?, 'daily', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (round_id, c.category, c.title, c.summary, dumps(ctx), ticker, name, question, band,
                     dumps(rates), dumps(sources), dumps(chart), utcnow()))
                published.append({"card_id": cur.lastrowid, "title": c.title, "ticker": ticker,
                                  "category": c.category, "flat_band": band})
        trace.add("publish", {"round_id": round_id, "cards": published})
        trace.finish("ok", round_id)
        return {"status": "ok", "round_id": round_id, "trade_date": td, "cards": published, "run_id": trace.id}
    except Exception as exc:
        trace.add("error", {"message": str(exc)})
        trace.finish("error")
        raise


async def _check_cards(h, trace: Trace, cards: list[m.CardDraft], by_id: dict, step: str = "factcheck"):
    code_problems = {}
    for i, c in enumerate(cards):
        hits = safety.directional_hits(f"{c.title} {c.summary}")
        if hits:
            code_problems[i] = [f"direction words: {', '.join(hits)}"]
    payload = []
    for i, c in enumerate(cards):
        srcs = [by_id[x] for x in c.headline_ids if x in by_id]
        payload.append(f"CARD {i}: {c.model_dump_json()}\n"
                       + safety.quote_untrusted(f"SOURCES FOR CARD {i}", srcs, ["id", "title", "summary"]))
    verdict, u = await run_agent(h.agents["factcheck"], "factcheck", "\n\n".join(payload), run_id=trace.id)
    vmap = {v.index: v for v in verdict.verdicts}
    good, failed = [], []
    for i, c in enumerate(cards):
        v = vmap.get(i)
        problems = (code_problems.get(i, []) + (v.problems if v else ["no verdict returned"]))
        if v and v.passed and i not in code_problems and any(x in by_id for x in c.headline_ids):
            good.append(c)
        else:
            failed.append((c, problems))
    trace.add(step, {"verdicts": [v.model_dump() for v in verdict.verdicts], "code_direction_check": code_problems,
                     "passed": len(good), "failed": len(failed)}, u)
    return good, failed


def _delete_round(con, round_id: int) -> None:
    ids = [r["id"] for r in rows(con.execute("SELECT id FROM cards WHERE round_id = ?", (round_id,)))]
    for cid in ids:
        for b in rows(con.execute("SELECT user_id, stake FROM bets WHERE card_id = ? AND practice = 0 "
                                  "AND settled_at IS NULL", (cid,))):
            con.execute("UPDATE users SET points = points + ? WHERE id = ?", (b["stake"], b["user_id"]))
        con.execute("DELETE FROM bets WHERE card_id = ?", (cid,))
        con.execute("UPDATE posts SET card_id = NULL WHERE card_id = ?", (cid,))
    con.execute("DELETE FROM cards WHERE round_id = ?", (round_id,))
    con.execute("DELETE FROM rounds WHERE id = ?", (round_id,))


# --- evening -------------------------------------------------------------------------

def default_evening_date(now: datetime | None = None) -> date:
    now = (now or datetime.now(timezone.utc)).astimezone(config.ET)
    today = now.date()
    if market.is_trading_day(today) and now >= market.settle_dt(today):
        return today
    return market.prev_trading_day(today)


async def run_evening(trade_date: date | None = None, *, live: bool = True, max_inflections: int = 3) -> dict:
    async with _run_lock:
        return await _evening(trade_date or default_evening_date(), live=live, max_inflections=max_inflections)


async def _evening(trade_date: date, *, live: bool, max_inflections: int) -> dict:
    td = trade_date.isoformat()
    trace = Trace("evening")
    try:
        # 1. Lock and settle (code).
        with connect() as con:
            game.lock_due_rounds(con)
            rnd = one(con.execute("SELECT * FROM rounds WHERE trade_date = ?", (td,)))
            settled = game.settle_round(con, rnd["id"]) if rnd and rnd["status"] != "settled" else None
        trace.add("settle", {"trade_date": td, "round_id": rnd["id"] if rnd else None, "result": settled})

        # 2. Minute bars and inflection scan (code).
        found = []
        with connect() as con:
            if live:
                news.refresh_live(con)
                for t in market.BAR_TICKERS:
                    try:
                        market.store_bars(con, t, market.fetch_minute_bars(t, "5d"))
                    except Exception as exc:
                        log.warning("bars %s failed: %s", t, exc)
            for t in ("^GSPC", "^IXIC"):
                for ev in inflection.detect(con, t, td):
                    if not con.execute("SELECT 1 FROM inflections WHERE ticker = ? AND ts = ?",
                                       (t, ev["ts"])).fetchone():
                        found.append(ev)
        found.sort(key=lambda e: -abs(e["move_pct"]))
        found = found[:max_inflections]
        trace.add("inflection_scan", {"trade_date": td, "new_events": found})

        h = None
        # 3. Inflection Analyst per event.
        for ev in found:
            h = h or await hub()
            await _attribute(h, trace, ev)

        # 4. Coach post-mortems for settled cards that lack one.
        with connect() as con:
            todo = rows(con.execute(
                "SELECT c.* FROM cards c JOIN rounds r ON r.id = c.round_id WHERE r.trade_date = ? "
                "AND c.outcome IS NOT NULL AND c.outcome != 'void' AND c.post_mortem IS NULL", (td,)))
        for card in todo:
            h = h or await hub()
            await _post_mortem(h, trace, card, td)
        trace.finish("ok", rnd["id"] if rnd else None)
        return {"status": "ok", "trade_date": td, "settled": settled, "inflections": len(found),
                "post_mortems": len(todo), "run_id": trace.id}
    except Exception as exc:
        trace.add("error", {"message": str(exc)})
        trace.finish("error")
        raise


async def _attribute(h, trace: Trace, ev: dict) -> None:
    t0 = datetime.fromisoformat(ev["ts"])
    lo = (t0 - timedelta(minutes=30)).isoformat(timespec="seconds")
    hi = (t0 + timedelta(minutes=5)).isoformat(timespec="seconds")
    q = ("SELECT id, source, title, summary, published_at FROM headlines "
         "WHERE published_at >= ? AND published_at <= ? ORDER BY published_at")
    with connect() as con:
        heads = rows(con.execute(q, (lo, hi)))
        if len(heads) < 3:  # thin coverage: pull that day's history from Google News once
            day = t0.astimezone(config.ET).date()
            news.refresh_google(con, after=day.isoformat(), before=(day + timedelta(days=1)).isoformat())
            heads = rows(con.execute(q, (lo, hi)))
    et = t0.astimezone(config.ET).strftime("%H:%M ET on %b %d")
    prompt = (f"Move: {market.ticker_name(ev['ticker'])} ({ev['ticker']}) moved {ev['move_pct']:+.2f}% in 15 minutes "
              f"starting {et} ({ev['ts']} UTC). Detector threshold was {ev['threshold']}%.\n"
              f"When calling tools use end_iso=\"{hi}\".\n\n"
              + (safety.quote_untrusted("HEADLINES", heads, ["id", "source", "published_at", "title", "summary"])
                 if heads else "No headlines were stored in this window."))
    att, u = await run_agent(h.agents["analyst"], "analyst", prompt, run_id=trace.id)
    head = next((x for x in heads if x["id"] == att.headline_id), None)
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO inflections (ticker, ts, direction, move_pct, headline_id, cause, mechanism, "
                    "confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ev["ticker"], ev["ts"], ev["direction"], ev["move_pct"], head["id"] if head else None,
                     att.cause, att.mechanism, att.confidence, utcnow()))
        card_id = None
        # A replay card only when the cause is credible and the headline doesn't give the answer away.
        if head and att.confidence in ("high", "medium") and not safety.directional_hits(head["title"]):
            card_id = _inflection_card(con, ev, head, att)
    trace.add("analyst", {"event": ev, "headlines_in_window": len(heads), "attribution": att.model_dump(),
                          "practice_card_id": card_id}, u)


def _inflection_card(con, ev: dict, head: dict, att: m.Attribution) -> int:
    ticker = ev["ticker"]
    name = market.ticker_name(ticker)
    pub = datetime.fromisoformat(head["published_at"]).astimezone(config.ET)
    ref = inflection.price_at(con, ticker, ev["ts"])
    close = inflection.price_at(con, ticker, ev["end"])
    band = 0.1
    move = (close / ref - 1) * 100 if ref and close else ev["move_pct"]
    pm = {"what_happened": f"{name} moved {move:+.2f}% in the 15 minutes after this headline.",
          "why": f"{att.cause} {att.mechanism}", "driver": f"attribution confidence: {att.confidence}"}
    cur = con.execute(
        "INSERT INTO cards (kind, category, title, summary, context, ticker, ticker_name, question, event_time, "
        "window_start, window_end, flat_band, base_rates, ref_price, close_price, move_pct, outcome, sources, "
        "post_mortem, chart, created_at) VALUES ('inflection', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (att.category, head["title"], f"Headline from {head['source']} at {pub.strftime('%H:%M ET, %b %d, %Y')}.",
         dumps({"background": "Inflection Replay: a real moment when the index turned sharply."}),
         ticker, name,
         f"In the 15 minutes after this headline, did {name} go Up, stay Flat (within ±{band}%), or go Down?",
         head["published_at"], ev["ts"], ev["end"], band, dumps({"up": 0.45, "flat": 0.1, "down": 0.45}),
         ref, close, round(move, 3), market.classify(move, band),
         dumps([{"title": head["title"], "source": head["source"], "published_at": head["published_at"],
                 "url": one(con.execute("SELECT url FROM headlines WHERE id = ?", (head["id"],)))["url"]}]),
         dumps(pm), dumps(inflection.intraday_chart(con, ticker, ev["ts"])), utcnow()))
    return cur.lastrowid


async def _post_mortem(h, trace: Trace, card: dict, td: str) -> None:
    with connect() as con:
        split = game.pools(con, card)
        infl = rows(con.execute("SELECT ticker, ts, direction, move_pct, cause, confidence FROM inflections "
                                "WHERE substr(ts, 1, 10) = ?", (td,)))
    facts = {"title": card["title"], "summary": card["summary"], "ticker": card["ticker"],
             "ticker_name": card["ticker_name"], "category": card["category"], "trade_date": td,
             "open": card["ref_price"], "close": card["close_price"], "move_pct": card["move_pct"],
             "flat_band_pct": card["flat_band"], "outcome": card["outcome"],
             "crowd_split": {k: v["share"] for k, v in split.items()}, "intraday_turning_points": infl}
    pm, u = await run_agent(h.agents["coach"], "coach", f"Card result:\n{dumps(facts)}", run_id=trace.id)
    with connect() as con:
        con.execute("UPDATE cards SET post_mortem = ?, lesson_rule = ? WHERE id = ?",
                    (dumps({"what_happened": pm.what_happened, "why": pm.why, "driver": pm.driver}),
                     pm.lesson_rule[:160], card["id"]))
    trace.add("coach", {"card_id": card["id"], "post_mortem": pm.model_dump()}, u)


# --- personal coaching (on demand, per-user budget) -----------------------------------

async def coach_bet(bet_id: int, user_id: int) -> dict:
    with connect() as con:
        bet = one(con.execute("SELECT * FROM bets WHERE id = ? AND user_id = ?", (bet_id, user_id)))
        if not bet:
            raise game.GameError("Bet not found.")
        if bet["coach_note"]:
            return loads(bet["coach_note"], {})
        if bet["settled_at"] is None:
            raise game.GameError("This bet hasn't settled yet.")
        card = one(con.execute("SELECT * FROM cards WHERE id = ?", (bet["card_id"],)))
    if card["outcome"] == bet["direction"]:
        raise game.GameError("You got this one right - coaching is for missed calls.")
    h = await hub()
    facts = {"card": {k: card[k] for k in ("title", "summary", "ticker_name", "move_pct", "flat_band", "outcome")},
             "post_mortem": loads(card["post_mortem"], {}), "player_pick": bet["direction"]}
    reason = bet["reason"] or "(the player gave no reason)"
    prompt = (f"{dumps(facts)}\n\n" + safety.quote_untrusted("PLAYER REASON", [{"reason": reason}], ["reason"]))
    note, _ = await run_agent(h.agents["coach_personal"], "coach_personal", prompt, user_id=user_id)
    data = note.model_dump()
    with connect() as con:
        con.execute("UPDATE bets SET coach_note = ? WHERE id = ?", (json.dumps(data), bet_id))
    return data


def recent_runs(limit: int = 20) -> list[dict]:
    with connect() as con:
        out = rows(con.execute("SELECT id, kind, started_at, finished_at, status, round_id FROM pipeline_runs "
                               "ORDER BY started_at DESC LIMIT ?", (limit,)))
    return out


def run_detail(run_id: str) -> dict | None:
    with connect() as con:
        r = one(con.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)))
    if r:
        r["trace"] = loads(r["trace"], [])
    return r
