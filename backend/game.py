"""The game engine: bets, parimutuel pools, settlement, League Points and tiers.

Plain code, zero tokens. Every number a player sees about the game is computed here.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

from . import config, market
from .db import loads, one, rows, utcnow


class GameError(ValueError):
    """A rule of the game was broken; the message is shown to the player."""


# --- tiers ---------------------------------------------------------------------------

def base_tier(lp: float) -> str:
    name = config.TIERS[0][0]
    for tier, need in config.TIERS:
        if lp >= need:
            name = tier
    return name


def tier_index(name: str) -> int:
    order = [t for t, _ in config.TIERS] + ["Grandmaster", "Challenger"]
    return order.index(name) if name in order else -1


def category_tiers(con: sqlite3.Connection, category: str) -> dict[int, str]:
    """user_id -> displayed tier in one category, with Grandmaster/Challenger for the top Masters."""
    data = rows(con.execute(
        "SELECT user_id, lp, bets FROM category_ratings WHERE category = ? ORDER BY lp DESC", (category,)))
    out: dict[int, str] = {}
    master_rank = 0
    for r in data:
        if r["bets"] < config.PLACEMENT_BETS:
            out[r["user_id"]] = "Unranked"
            continue
        tier = base_tier(r["lp"])
        if tier == "Master":
            master_rank += 1
            if master_rank == 1:
                tier = "Challenger"
            elif master_rank <= 3:
                tier = "Grandmaster"
        out[r["user_id"]] = tier
    return out


def user_tier(con: sqlite3.Connection, user_id: int, category: str) -> str:
    return category_tiers(con, category).get(user_id, "Unranked")


def overall(con: sqlite3.Connection, user_id: int) -> dict:
    data = rows(con.execute(
        "SELECT lp FROM category_ratings WHERE user_id = ? AND bets >= ?", (user_id, config.PLACEMENT_BETS)))
    if not data:
        return {"lp": None, "tier": "Unranked"}
    lp = sum(r["lp"] for r in data) / len(data)
    return {"lp": round(lp, 1), "tier": base_tier(lp)}


def ensure_ratings(con: sqlite3.Connection, user_id: int) -> None:
    for c in config.CATEGORIES:
        con.execute("INSERT OR IGNORE INTO category_ratings (user_id, category, lp) VALUES (?, ?, ?)",
                    (user_id, c, config.START_LP))


# --- pools and odds ------------------------------------------------------------------

def pools(con: sqlite3.Connection, card: dict) -> dict:
    """Live pools including the house seed spread by base rate."""
    rates = loads(card["base_rates"], {"up": 0.4, "flat": 0.2, "down": 0.4})
    seed = {k: config.HOUSE_SEED * rates.get(k, 0) for k in ("up", "flat", "down")}
    players = {k: 0 for k in seed}
    counts = {k: 0 for k in seed}
    for r in rows(con.execute(
            "SELECT direction, SUM(stake) s, COUNT(*) n FROM bets WHERE card_id = ? AND practice = 0 "
            "GROUP BY direction", (card["id"],))):
        players[r["direction"]] = r["s"] or 0
        counts[r["direction"]] = r["n"]
    total = sum(seed.values()) + sum(players.values())
    out = {}
    for k in seed:
        pool = seed[k] + players[k]
        out[k] = {"players": players[k], "bettors": counts[k], "pool": round(pool, 1),
                  "share": round(pool / total, 3) if total else 0,
                  "multiplier": round(total / pool, 2) if pool else None}
    return out


# --- placing bets --------------------------------------------------------------------

def _card(con: sqlite3.Connection, card_id: int) -> dict:
    card = one(con.execute("SELECT * FROM cards WHERE id = ?", (card_id,)))
    if not card:
        raise GameError("That card does not exist.")
    return card


def card_mode(con: sqlite3.Connection, card: dict, now: datetime | None = None) -> str:
    """'live' (betting open), 'locked' (waiting for settlement), or 'practice' (already settled)."""
    if card["outcome"]:
        return "practice"
    rnd = one(con.execute("SELECT * FROM rounds WHERE id = ?", (card["round_id"],))) if card["round_id"] else None
    if not rnd:
        return "locked"
    now = now or datetime.now(timezone.utc)
    if rnd["status"] == "open" and now < datetime.fromisoformat(rnd["lock_at"]):
        return "live"
    return "locked"


def place_bet(con: sqlite3.Connection, user_id: int, card_id: int, direction: str, stake: int,
              reason: str, rule_id: int | None = None, now: datetime | None = None) -> dict:
    direction = (direction or "").lower()
    if direction not in ("up", "flat", "down"):
        raise GameError("Pick Up, Flat or Down.")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise GameError("Write a one-line reason. Rules beat gut feelings.")
    if len(reason) > 280:
        raise GameError("Keep the reason under 280 characters.")
    card = _card(con, card_id)
    if con.execute("SELECT 1 FROM bets WHERE user_id = ? AND card_id = ?", (user_id, card_id)).fetchone():
        raise GameError("You already bet on this card.")
    if rule_id is not None and not con.execute(
            "SELECT 1 FROM rules WHERE id = ? AND user_id = ?", (rule_id, user_id)).fetchone():
        raise GameError("That rule is not in your rulebook.")
    mode = card_mode(con, card, now)
    if mode == "locked":
        raise GameError("Betting on this card is closed (locked at 09:25 ET).")
    ensure_ratings(con, user_id)

    if mode == "practice":
        return _practice_bet(con, user_id, card, direction, reason, rule_id)

    stake = int(stake)
    if not config.MIN_STAKE <= stake <= config.MAX_STAKE:
        raise GameError(f"Stake must be between {config.MIN_STAKE} and {config.MAX_STAKE} points.")
    _daily_allowance(con, user_id)
    user = one(con.execute("SELECT points FROM users WHERE id = ?", (user_id,)))
    if user["points"] < stake:
        raise GameError("Not enough points for that stake.")
    con.execute("UPDATE users SET points = points - ? WHERE id = ?", (stake, user_id))
    con.execute(
        "INSERT INTO bets (user_id, card_id, direction, stake, reason, rule_id, practice, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?)", (user_id, card_id, direction, stake, reason, rule_id, utcnow()))
    return {"mode": "live", "card_id": card_id, "direction": direction, "stake": stake,
            "pools": pools(con, card)}


def _daily_allowance(con: sqlite3.Connection, user_id: int) -> None:
    """+100 points and a streak update on the first live bet of each ET day."""
    today = market.now_et().date()
    user = one(con.execute("SELECT last_play_date, streak FROM users WHERE id = ?", (user_id,)))
    if user["last_play_date"] == today.isoformat():
        return
    streak = 1
    if user["last_play_date"]:
        last = date.fromisoformat(user["last_play_date"])
        if last >= market.prev_trading_day(today):
            streak = user["streak"] + 1
    con.execute("UPDATE users SET points = points + ?, streak = ?, last_play_date = ? WHERE id = ?",
                (config.DAILY_ALLOWANCE, streak, today.isoformat(), user_id))


def _practice_bet(con, user_id, card, direction, reason, rule_id) -> dict:
    """Replay: settles instantly against the known outcome, scored against base-rate odds."""
    if card["outcome"] == "void":
        raise GameError("This card was voided and can't be replayed.")
    rates = loads(card["base_rates"], {"up": 0.4, "flat": 0.2, "down": 0.4})
    p = rates.get(direction, 1 / 3)
    correct = direction == card["outcome"]
    lp = lp_change(correct, p) * config.PRACTICE_LP_FACTOR
    con.execute(
        "INSERT INTO bets (user_id, card_id, direction, stake, reason, rule_id, practice, crowd_share, payout, "
        "lp_change, created_at, settled_at) VALUES (?, ?, ?, 0, ?, ?, 1, ?, 0, ?, ?, ?)",
        (user_id, card["id"], direction, reason, rule_id, p, lp, utcnow(), utcnow()))
    _apply_rating(con, user_id, card["category"], correct, lp)
    _apply_rule(con, rule_id, correct)
    return {"mode": "practice", "card_id": card["id"], "direction": direction, "correct": correct,
            "outcome": card["outcome"], "move_pct": card["move_pct"], "lp_change": round(lp, 1),
            "post_mortem": card["post_mortem"], "lesson_rule": card["lesson_rule"]}


def lp_change(correct: bool, p: float) -> float:
    """Fair scoring: +K(1-p) if right, -K*p if wrong, where p is the crowd's share on your pick."""
    return config.LP_K * (1 - p) if correct else -config.LP_K * p


def _apply_rating(con, user_id, category, correct, lp) -> None:
    con.execute(
        "UPDATE category_ratings SET lp = MAX(0, lp + ?), wins = wins + ?, losses = losses + ?, bets = bets + 1 "
        "WHERE user_id = ? AND category = ?", (lp, int(correct), int(not correct), user_id, category))


def _apply_rule(con, rule_id, correct) -> None:
    if rule_id:
        con.execute("UPDATE rules SET uses = uses + 1, wins = wins + ? WHERE id = ?", (int(correct), rule_id))


# --- rounds: lock and settle ---------------------------------------------------------

def lock_due_rounds(con: sqlite3.Connection, now: datetime | None = None) -> list[int]:
    now = now or datetime.now(timezone.utc)
    locked = []
    for r in rows(con.execute("SELECT id, lock_at FROM rounds WHERE status = 'open'")):
        if now >= datetime.fromisoformat(r["lock_at"]):
            con.execute("UPDATE rounds SET status = 'locked' WHERE id = ?", (r["id"],))
            locked.append(r["id"])
    return locked


def settle_card(con: sqlite3.Connection, card: dict, open_px: float | None, close_px: float | None) -> dict:
    """Settle one daily card from the official open and close. Missing prices void it and refund stakes."""
    if open_px and close_px:
        move = (close_px / open_px - 1) * 100
        outcome = market.classify(move, card["flat_band"])
    else:
        move, outcome = None, "void"
    final = pools(con, card)
    total = sum(v["pool"] for v in final.values())
    con.execute("UPDATE cards SET ref_price = ?, close_price = ?, move_pct = ?, outcome = ? WHERE id = ?",
                (open_px, close_px, round(move, 3) if move is not None else None, outcome, card["id"]))
    bets = rows(con.execute("SELECT * FROM bets WHERE card_id = ? AND practice = 0 AND settled_at IS NULL",
                            (card["id"],)))
    for b in bets:
        p = final[b["direction"]]["share"]
        if outcome == "void":
            payout, lp, correct = b["stake"], 0.0, None
        else:
            correct = b["direction"] == outcome
            win_pool = final[outcome]["pool"]
            payout = round(b["stake"] * total / win_pool) if correct else 0
            lp = lp_change(correct, p)
        con.execute("UPDATE users SET points = points + ? WHERE id = ?", (payout, b["user_id"]))
        con.execute("UPDATE bets SET crowd_share = ?, payout = ?, lp_change = ?, settled_at = ? WHERE id = ?",
                    (p, payout, lp, utcnow(), b["id"]))
        if correct is not None:
            ensure_ratings(con, b["user_id"])
            _apply_rating(con, b["user_id"], card["category"], correct, lp)
            _apply_rule(con, b["rule_id"], correct)
    return {"card_id": card["id"], "outcome": outcome, "move_pct": move, "bets": len(bets)}


def settle_round(con: sqlite3.Connection, round_id: int, force_void: bool = False) -> dict | None:
    """Settle every card in a locked round once that day's close exists. Returns None if not ready."""
    rnd = one(con.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)))
    if not rnd or rnd["status"] == "settled":
        return None
    trade_date = rnd["trade_date"]
    cards = rows(con.execute("SELECT * FROM cards WHERE round_id = ? AND outcome IS NULL", (round_id,)))
    prices = {}
    for c in cards:
        daily = market.refresh_daily(con, c["ticker"], 15)
        row = next((d for d in daily if d["date"] == trade_date), None)
        prices[c["id"]] = (row["open"], row["close"]) if row else (None, None)
    if not force_void and any(v == (None, None) for v in prices.values()):
        return None  # close not published yet
    results = [settle_card(con, c, *prices[c["id"]]) for c in cards]
    con.execute("UPDATE rounds SET status = 'settled', settled_at = ? WHERE id = ?", (utcnow(), round_id))
    return {"round_id": round_id, "trade_date": trade_date, "cards": results}


# --- profile, rulebook, leaderboard --------------------------------------------------

def profile(con: sqlite3.Connection, user_id: int) -> dict:
    ensure_ratings(con, user_id)
    user = one(con.execute("SELECT id, username, points, streak, is_admin, created_at FROM users WHERE id = ?",
                           (user_id,)))
    cats = []
    for r in rows(con.execute("SELECT * FROM category_ratings WHERE user_id = ?", (user_id,))):
        cats.append({"category": r["category"], "lp": round(r["lp"], 1), "wins": r["wins"],
                     "losses": r["losses"], "bets": r["bets"], "tier": user_tier(con, user_id, r["category"]),
                     "placement_left": max(0, config.PLACEMENT_BETS - r["bets"])})
    cats.sort(key=lambda c: config.CATEGORIES.index(c["category"]))
    stats = one(con.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN lp_change > 0 THEN 1 ELSE 0 END) w, "
        "SUM(CASE WHEN practice = 0 THEN 1 ELSE 0 END) live FROM bets WHERE user_id = ? AND settled_at IS NOT NULL",
        (user_id,)))
    return {**user, "is_admin": bool(user["is_admin"]), "categories": cats, "overall": overall(con, user_id),
            "settled_bets": stats["n"] or 0, "correct_bets": stats["w"] or 0, "live_bets": stats["live"] or 0}


def rulebook(con: sqlite3.Connection, user_id: int) -> list[dict]:
    out = rows(con.execute("SELECT * FROM rules WHERE user_id = ? ORDER BY uses DESC, id DESC", (user_id,)))
    for r in out:
        r["win_rate"] = round(r["wins"] / r["uses"], 2) if r["uses"] else None
    return out


def add_rule(con: sqlite3.Connection, user_id: int, text: str, category: str, source: str = "user") -> dict:
    text = (text or "").strip()
    if not 5 <= len(text) <= 200:
        raise GameError("A rule should be 5-200 characters.")
    if category not in config.CATEGORIES:
        raise GameError("Unknown category.")
    if con.execute("SELECT COUNT(*) FROM rules WHERE user_id = ?", (user_id,)).fetchone()[0] >= 50:
        raise GameError("Your rulebook is full (50 rules). Delete one first.")
    cur = con.execute("INSERT INTO rules (user_id, text, category, source, created_at) VALUES (?, ?, ?, ?, ?)",
                      (user_id, text, category, source, utcnow()))
    return one(con.execute("SELECT * FROM rules WHERE id = ?", (cur.lastrowid,)))


def leaderboard(con: sqlite3.Connection, category: str | None = None, limit: int = 50) -> list[dict]:
    if category:
        tiers = category_tiers(con, category)
        data = rows(con.execute(
            "SELECT u.id user_id, u.username, u.is_bot, r.lp, r.wins, r.losses, r.bets FROM category_ratings r "
            "JOIN users u ON u.id = r.user_id WHERE r.category = ? AND r.bets > 0 ORDER BY r.lp DESC LIMIT ?",
            (category, limit)))
        for d in data:
            d["tier"] = tiers.get(d["user_id"], "Unranked")
            d["lp"] = round(d["lp"], 1)
        return data
    out = []
    for u in rows(con.execute("SELECT id user_id, username, is_bot, points, streak FROM users")):
        o = overall(con, u["user_id"])
        rec = one(con.execute(
            "SELECT SUM(wins) w, SUM(losses) l FROM category_ratings WHERE user_id = ?", (u["user_id"],)))
        if (rec["w"] or 0) + (rec["l"] or 0) == 0:
            continue
        out.append({**u, **o, "wins": rec["w"] or 0, "losses": rec["l"] or 0})
    out.sort(key=lambda d: (d["lp"] is None, -(d["lp"] or 0), -d["points"]))
    return out[:limit]


def record_for(con: sqlite3.Connection, user_id: int, category: str) -> str:
    r = one(con.execute("SELECT wins, losses FROM category_ratings WHERE user_id = ? AND category = ?",
                        (user_id, category)))
    return f"{r['wins']}-{r['losses']}" if r else "0-0"


def card_public(con: sqlite3.Connection, card: dict, user_id: int | None = None) -> dict:
    """A card as the player sees it: odds, their bet, and the outcome only once settled."""
    mode = card_mode(con, card)
    out = {k: card[k] for k in ("id", "round_id", "kind", "category", "title", "summary", "ticker", "ticker_name",
                                "question", "event_time", "flat_band", "created_at")}
    out["context"] = loads(card["context"], {})
    out["sources"] = loads(card["sources"], [])
    out["chart"] = loads(card["chart"], [])
    out["base_rates"] = loads(card["base_rates"], {})
    out["mode"] = mode
    out["pools"] = pools(con, card)
    rnd = one(con.execute("SELECT trade_date, lock_at FROM rounds WHERE id = ?", (card["round_id"],))) \
        if card["round_id"] else None
    out["trade_date"] = rnd["trade_date"] if rnd else None
    out["lock_at"] = rnd["lock_at"] if rnd else None
    my = one(con.execute("SELECT * FROM bets WHERE card_id = ? AND user_id = ?", (card["id"], user_id))) \
        if user_id else None
    out["my_bet"] = my
    if card["outcome"]:
        out.update({"outcome": card["outcome"], "move_pct": card["move_pct"], "ref_price": card["ref_price"],
                    "close_price": card["close_price"]})
        # The lesson is shown once you have committed a bet (live or replay), never before.
        if my:
            out.update({"post_mortem": card["post_mortem"], "lesson_rule": card["lesson_rule"]})
    return out


def context_for_card(con: sqlite3.Connection, ticker: str, before: str) -> tuple[float, dict, list, dict]:
    daily = market.refresh_daily(con, ticker, 120)
    band, rates = market.flat_band_and_base_rates(daily, before)
    chart = market.mini_chart(daily, before)
    last = chart[-1] if chart else None
    ctx = {"last_close": last["c"] if last else None, "last_close_date": last["d"] if last else None}
    return band, rates, chart, ctx
