"""Settlement math, betting rules, lock time and tiers. No network, no model calls."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp()) / "test.db"
os.environ["BTWS_DB_PATH"] = str(TMP)

from backend import config, game, market  # noqa: E402
from backend.db import connect, dumps, init_db, one, utcnow  # noqa: E402


@pytest.fixture()
def con():
    if TMP.exists():
        TMP.unlink()
    init_db(TMP)
    with connect(TMP) as c:
        yield c


def _user(con, name):
    cur = con.execute("INSERT INTO users (username, pin_hash, salt, points, created_at) VALUES (?, 'x', 'y', ?, ?)",
                      (name, config.START_POINTS, utcnow()))
    game.ensure_ratings(con, cur.lastrowid)
    return cur.lastrowid


def _round(con, lock_in_minutes=60, rates=None):
    lock = (datetime.now(timezone.utc) + timedelta(minutes=lock_in_minutes)).isoformat(timespec="seconds")
    rid = con.execute("INSERT INTO rounds (trade_date, status, lock_at, created_at) VALUES ('2026-10-14', 'open', ?, ?)",
                      (lock, utcnow())).lastrowid
    cid = con.execute(
        "INSERT INTO cards (round_id, kind, category, title, summary, ticker, question, flat_band, base_rates, created_at) "
        "VALUES (?, 'daily', 'macro', 't', 's', '^GSPC', 'q', 0.2, ?, ?)",
        (rid, dumps(rates or {"up": 0.4, "flat": 0.2, "down": 0.4}), utcnow())).lastrowid
    return rid, cid


def test_classify_flat_band():
    assert market.classify(0.25, 0.2) == "up"
    assert market.classify(-0.25, 0.2) == "down"
    assert market.classify(0.2, 0.2) == "flat"
    assert market.classify(-0.1, 0.2) == "flat"


def test_lp_is_fair():
    # A player who matches the crowd's probability gains nothing on average.
    p = 0.3
    ev = p * game.lp_change(True, p) + (1 - p) * game.lp_change(False, p)
    assert abs(ev) < 1e-9
    assert game.lp_change(True, 0.2) == pytest.approx(24)
    assert game.lp_change(False, 0.7) == pytest.approx(-21)


def test_parimutuel_payout_and_points(con):
    a, b, c = _user(con, "alice"), _user(con, "bob"), _user(con, "carol")
    rid, cid = _round(con)
    game.place_bet(con, a, cid, "up", 100, "rates fell")
    game.place_bet(con, b, cid, "down", 50, "hot data")
    game.place_bet(con, c, cid, "up", 50, "momentum")
    card = one(con.execute("SELECT * FROM cards WHERE id = ?", (cid,)))
    pools = game.pools(con, card)
    total = sum(v["pool"] for v in pools.values())
    assert total == pytest.approx(300)  # 200 player points + 100 house seed
    con.execute("UPDATE rounds SET status = 'locked' WHERE id = ?", (rid,))
    res = game.settle_card(con, card, 100.0, 100.5)  # +0.5% > 0.2% band -> up
    assert res["outcome"] == "up"
    win_pool = pools["up"]["pool"]  # 150 players + 40 seed
    pts = {u: one(con.execute("SELECT points FROM users WHERE id = ?", (u,)))["points"] for u in (a, b, c)}
    allowance = config.DAILY_ALLOWANCE
    assert pts[a] == config.START_POINTS + allowance - 100 + round(100 * total / win_pool)
    assert pts[b] == config.START_POINTS + allowance - 50
    assert pts[c] == config.START_POINTS + allowance - 50 + round(50 * total / win_pool)
    r = one(con.execute("SELECT * FROM category_ratings WHERE user_id = ? AND category = 'macro'", (b,)))
    assert r["losses"] == 1 and r["lp"] < config.START_LP


def test_void_refunds(con):
    a = _user(con, "dave")
    rid, cid = _round(con)
    game.place_bet(con, a, cid, "flat", 40, "quiet day")
    card = one(con.execute("SELECT * FROM cards WHERE id = ?", (cid,)))
    game.settle_card(con, card, None, None)
    assert one(con.execute("SELECT points FROM users WHERE id = ?", (a,)))["points"] == \
        config.START_POINTS + config.DAILY_ALLOWANCE


def test_rules_enforced(con):
    a = _user(con, "erin")
    rid, cid = _round(con)
    with pytest.raises(game.GameError, match="280"):
        game.place_bet(con, a, cid, "up", 50, "x" * 300)
    with pytest.raises(game.GameError, match="Stake"):
        game.place_bet(con, a, cid, "up", 500, "too big")
    with pytest.raises(game.GameError, match="Pick"):
        game.place_bet(con, a, cid, "sideways", 50, "bad pick")
    game.place_bet(con, a, cid, "up", 50, "")  # a reason is optional
    with pytest.raises(game.GameError, match="already"):
        game.place_bet(con, a, cid, "down", 50, "again")


def test_late_bet_rejected(con):
    a = _user(con, "frank")
    rid, cid = _round(con, lock_in_minutes=-1)  # lock time already passed
    with pytest.raises(game.GameError, match="closed"):
        game.place_bet(con, a, cid, "up", 50, "too late")


def test_practice_bet_settles_instantly(con):
    a = _user(con, "gina")
    rid, cid = _round(con)
    con.execute("UPDATE cards SET outcome = 'down', move_pct = -0.8 WHERE id = ?", (cid,))
    res = game.place_bet(con, a, cid, "down", 0, "hot CPI hurts stocks")
    assert res["mode"] == "practice" and res["correct"]
    assert res["lp_change"] == pytest.approx(30 * 0.6 / 3, abs=0.01)
    assert one(con.execute("SELECT points FROM users WHERE id = ?", (a,)))["points"] == config.START_POINTS


def test_tiers():
    assert game.base_tier(0) == "Iron"
    assert game.base_tier(150) == "Bronze"
    assert game.base_tier(650) == "Diamond"
    assert game.base_tier(900) == "Master"


def test_target_trade_date():
    et = config.ET
    tue_morning = datetime(2026, 10, 13, 8, 0, tzinfo=et)
    tue_after = datetime(2026, 10, 13, 9, 30, tzinfo=et)
    fri_evening = datetime(2026, 10, 16, 18, 0, tzinfo=et)
    assert market.target_trade_date(tue_morning).isoformat() == "2026-10-13"
    assert market.target_trade_date(tue_after).isoformat() == "2026-10-14"
    assert market.target_trade_date(fri_evening).isoformat() == "2026-10-19"


def test_flat_band_from_history():
    daily = [{"date": f"2026-09-{i:02d}", "open": 100, "close": 100 + (1 if i % 2 else -1)} for i in range(1, 29)]
    band, rates = market.flat_band_and_base_rates(daily)
    assert band == pytest.approx(0.3)  # 0.30 x mean |1%|
    assert abs(sum(rates.values()) - 1) < 0.01
