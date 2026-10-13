"""Tier-gated, Reddit-style community. Every post passes the rule filter, then the Moderator agent."""

from __future__ import annotations

import logging

from . import config, game, safety
from .agents import hub
from .config import AgentUnavailable
from .db import connect, log_safety, one, rows, utcnow
from .llm import BudgetExceeded, ContentBlocked, run_agent

log = logging.getLogger(__name__)
MAX_DEPTH = 3
REPORTS_TO_HIDE = 3


def _settled_bets(con, user_id: int) -> int:
    return con.execute("SELECT COUNT(*) FROM bets WHERE user_id = ? AND settled_at IS NOT NULL",
                       (user_id,)).fetchone()[0]


def _depth(con, parent_id: int | None) -> int:
    d = 0
    while parent_id:
        d += 1
        r = con.execute("SELECT parent_id FROM posts WHERE id = ?", (parent_id,)).fetchone()
        parent_id = r[0] if r else None
    return d


async def create_post(user: dict, *, body: str, title: str | None = None, card_id: int | None = None,
                      category: str | None = None, parent_id: int | None = None) -> dict:
    body = (body or "").strip()
    title = (title or "").strip() or None
    with connect() as con:
        parent = one(con.execute("SELECT * FROM posts WHERE id = ?", (parent_id,))) if parent_id else None
        if parent_id and not parent:
            raise game.GameError("The post you are replying to doesn't exist.")
        if parent:
            card_id, category, title = parent["card_id"], parent["category"], None
        card = one(con.execute("SELECT * FROM cards WHERE id = ?", (card_id,))) if card_id else None
        if card_id and not card:
            raise game.GameError("Card not found.")
        if card:
            category = card["category"]
        if category not in config.CATEGORIES:
            raise game.GameError("Pick a category.")
        if _depth(con, parent_id) >= MAX_DEPTH:
            raise game.GameError("Replies can nest up to 3 levels.")
        if not user["is_admin"]:
            if title:  # an insight thread
                tier = game.user_tier(con, user["id"], category)
                if game.tier_index(tier) < game.tier_index(config.POST_THREAD_MIN_TIER):
                    raise game.GameError(
                        f"Insight threads in {category} need {config.POST_THREAD_MIN_TIER} tier or higher there "
                        f"(you are {tier}). You can still comment.")
            elif _settled_bets(con, user["id"]) < 1:
                raise game.GameError("Settle at least one bet (live or replay) before commenting.")

        verdict, reason = safety.post_rule_filter(title or "", body)
        if verdict == "block":
            cur = con.execute(
                "INSERT INTO posts (user_id, card_id, category, parent_id, title, body, status, mod_reason, mod_by, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, 'rejected', ?, 'rule_filter', ?)",
                (user["id"], card_id, category, parent_id, title, body, reason, utcnow()))
            log_safety(con, "post_blocked_rule", f"post {cur.lastrowid}: {reason}", user["id"])
            return {"id": cur.lastrowid, "status": "rejected", "reason": reason, "by": "rule_filter"}

    status, reason, by = await _moderate(user, title, body)
    with connect() as con:
        cur = con.execute(
            "INSERT INTO posts (user_id, card_id, category, parent_id, title, body, status, mod_reason, mod_by, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user["id"], card_id, category, parent_id, title, body, status, reason, by, utcnow()))
        if status in ("rejected", "flagged"):
            log_safety(con, f"post_{status}_moderator", f"post {cur.lastrowid}: {reason}", user["id"])
    return {"id": cur.lastrowid, "status": status, "reason": reason, "by": by}


async def _moderate(user: dict, title: str | None, body: str) -> tuple[str, str, str]:
    pumps = safety.pump_hits(f"{title or ''} {body}")
    try:
        h = await hub()
        prompt = ((f"Code pre-check found promotional phrases: {pumps}\n\n" if pumps else "")
                  + safety.quote_untrusted("POST", [{"title": title or "(comment)", "body": body}], ["title", "body"]))
        v, _ = await run_agent(h.agents["moderator"], "moderator", prompt, user_id=user["id"])
    except BudgetExceeded as exc:
        return "hidden", f"Held for review: {exc}", "budget_guard"
    except ContentBlocked:
        return "rejected", "Blocked by the AI provider's safety filter.", "provider_filter"
    except AgentUnavailable:
        return "hidden", "Held for review: the moderator is offline.", "fail_safe"
    except Exception as exc:  # fail closed: never publish unreviewed text
        log.warning("moderator failed: %s", exc)
        return "hidden", "Held for review: moderation failed.", "fail_safe"
    status = {"approve": "visible", "flag": "flagged", "reject": "rejected"}[v.decision]
    return status, v.reason, "moderator"


def _decorate(con, posts: list[dict], viewer_id: int | None) -> list[dict]:
    tiers_cache: dict[str, dict[int, str]] = {}
    for p in posts:
        cat = p["category"]
        if cat not in tiers_cache:
            tiers_cache[cat] = game.category_tiers(con, cat)
        p["author_tier"] = tiers_cache[cat].get(p["user_id"], "Unranked")
        p["author_record"] = game.record_for(con, p["user_id"], cat)
        p["score"] = round(con.execute("SELECT COALESCE(SUM(value * weight), 0) FROM votes WHERE post_id = ?",
                                       (p["id"],)).fetchone()[0], 2)
        mine = con.execute("SELECT value FROM votes WHERE post_id = ? AND user_id = ?",
                           (p["id"], viewer_id)).fetchone() if viewer_id else None
        p["my_vote"] = mine[0] if mine else 0
        p["author_bet"] = None
        if p["card_id"]:
            card = one(con.execute("SELECT * FROM cards WHERE id = ?", (p["card_id"],)))
            # Bets are revealed only after lock, so nobody can herd the crowd while betting is open.
            if card and game.card_mode(con, card) != "live":
                b = one(con.execute("SELECT direction, stake FROM bets WHERE card_id = ? AND user_id = ?",
                                    (p["card_id"], p["user_id"])))
                p["author_bet"] = b
            p["card_title"] = card["title"] if card else None
            p["card_outcome"] = card["outcome"] if card else None
        p["is_mine"] = p["user_id"] == viewer_id
        if p["status"] == "rejected" and not p["is_mine"]:
            p["body"] = "[removed by moderation]"
    return posts


def list_threads(viewer_id: int | None, category: str | None = None, card_id: int | None = None,
                 limit: int = 50) -> list[dict]:
    sql = ("SELECT p.*, u.username FROM posts p JOIN users u ON u.id = p.user_id WHERE p.parent_id IS NULL "
           "AND (p.status IN ('visible', 'flagged') OR p.user_id = ?)")
    args: list = [viewer_id or -1]
    if category:
        sql += " AND p.category = ?"
        args.append(category)
    if card_id:
        sql += " AND p.card_id = ?"
        args.append(card_id)
    sql += " ORDER BY p.created_at DESC LIMIT ?"
    args.append(limit)
    with connect() as con:
        posts = _decorate(con, rows(con.execute(sql, args)), viewer_id)
        for p in posts:
            p["replies"] = _count_replies(con, p["id"])
    posts.sort(key=lambda p: (p["title"] is None, -p["score"]))  # insights first; stable keeps newest first
    return posts


def _count_replies(con, post_id: int) -> int:
    kids = [r[0] for r in con.execute("SELECT id FROM posts WHERE parent_id = ? AND status IN ('visible','flagged')",
                                      (post_id,))]
    return len(kids) + sum(_count_replies(con, k) for k in kids)


def thread(viewer_id: int | None, post_id: int) -> dict | None:
    with connect() as con:
        root = one(con.execute("SELECT p.*, u.username FROM posts p JOIN users u ON u.id = p.user_id WHERE p.id = ?",
                               (post_id,)))
        if not root:
            return None
        allp = [root]
        frontier = [post_id]
        while frontier:
            kids = rows(con.execute(
                f"SELECT p.*, u.username FROM posts p JOIN users u ON u.id = p.user_id WHERE p.parent_id IN "
                f"({','.join('?' * len(frontier))}) AND (p.status IN ('visible','flagged') OR p.user_id = ?) "
                f"ORDER BY p.created_at", [*frontier, viewer_id or -1]))
            allp += kids
            frontier = [k["id"] for k in kids]
        _decorate(con, allp, viewer_id)
    by_id = {p["id"]: {**p, "children": []} for p in allp}
    for p in by_id.values():
        if p["parent_id"] in by_id and p["id"] != post_id:
            by_id[p["parent_id"]]["children"].append(p)
    return by_id[post_id]


def vote(user: dict, post_id: int, value: int) -> dict:
    if value not in (-1, 0, 1):
        raise game.GameError("Vote must be -1, 0 or 1.")
    with connect() as con:
        post = one(con.execute("SELECT * FROM posts WHERE id = ?", (post_id,)))
        if not post:
            raise game.GameError("Post not found.")
        if post["user_id"] == user["id"]:
            raise game.GameError("You can't vote on your own post.")
        if not user["is_admin"] and _settled_bets(con, user["id"]) < config.VOTE_MIN_BETS:
            raise game.GameError(f"Voting unlocks after {config.VOTE_MIN_BETS} settled bets.")
        if value == 0:
            con.execute("DELETE FROM votes WHERE post_id = ? AND user_id = ?", (post_id, user["id"]))
        else:
            # Higher-tier voters count a little more (Iron 1.0 ... Challenger 3.25).
            weight = 1 + 0.25 * max(0, game.tier_index(game.user_tier(con, user["id"], post["category"])))
            con.execute("INSERT OR REPLACE INTO votes (post_id, user_id, value, weight) VALUES (?, ?, ?, ?)",
                        (post_id, user["id"], value, weight))
        score = con.execute("SELECT COALESCE(SUM(value * weight), 0) FROM votes WHERE post_id = ?",
                            (post_id,)).fetchone()[0]
    return {"post_id": post_id, "score": round(score, 2), "my_vote": value}


def report(user: dict, post_id: int) -> dict:
    with connect() as con:
        if not con.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone():
            raise game.GameError("Post not found.")
        con.execute("INSERT OR IGNORE INTO reports (post_id, user_id) VALUES (?, ?)", (post_id, user["id"]))
        n = con.execute("SELECT COUNT(*) FROM reports WHERE post_id = ?", (post_id,)).fetchone()[0]
        hidden = n >= REPORTS_TO_HIDE
        if hidden:
            con.execute("UPDATE posts SET status = 'hidden', mod_reason = 'Hidden after 3 reports', mod_by = 'reports' "
                        "WHERE id = ? AND status IN ('visible','flagged')", (post_id,))
            log_safety(con, "post_hidden_reports", f"post {post_id} hidden after {n} reports", user["id"])
    return {"post_id": post_id, "reports": n, "hidden": hidden}
