"""Free, key-less news: publisher RSS feeds and Google News RSS search (live and history).

Headlines are untrusted text. They are cleaned and truncated here, scanned for
injection patterns in safety.py, and only ever shown to agents as quoted data.
"""

from __future__ import annotations

import html
import logging
import re
import sqlite3
import time as _time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import safety
from .db import rows, utcnow

log = logging.getLogger(__name__)

RSS_FEEDS = {
    "Yahoo Finance": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC,^IXIC,^DJI&region=US&lang=en-US",
    "CNBC Top News": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "CNBC Economy": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "CNBC Earnings": "https://www.cnbc.com/id/15839135/device/rss/rss.html",
    "CNBC World": "https://www.cnbc.com/id/100727362/device/rss/rss.html",
    "MarketWatch Top": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "MarketWatch Pulse": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
}

# Words that make a headline likely to move U.S. markets, with a rough weight.
MARKET_TERMS = {
    3: ["fed", "fomc", "powell", "rate cut", "rate hike", "cpi", "inflation", "jobs report", "payrolls",
        "unemployment", "gdp", "tariff", "earnings", "guidance", "recession", "treasury", "yield"],
    2: ["s&p", "nasdaq", "dow", "stocks", "futures", "oil", "opec", "china", "sanction", "war", "strike",
        "default", "bank", "chip", "nvidia", "apple", "microsoft", "tesla", "amazon", "meta", "alphabet",
        "merger", "acquisition", "ipo", "layoffs", "shutdown", "election", "ukraine", "russia", "israel",
        "iran", "taiwan", "dollar", "gold", "bitcoin", "retail sales", "pmi", "housing"],
    1: ["market", "shares", "investor", "economy", "price", "sales", "profit", "revenue", "forecast"],
}
NOISE_TERMS = ["recipe", "celebrity", "nfl", "nba", "mlb", "horoscope", "travel deal", "best credit card",
               "mortgage rates today", "how to save", "podcast", "video:", "watch:", "quiz"]


def _clean(text: str | None, limit: int) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _to_utc_iso(value) -> str | None:
    try:
        dt = parsedate_to_datetime(value) if isinstance(value, str) else value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, AttributeError):
        return None


def store_headlines(con: sqlite3.Connection, items: list[dict]) -> int:
    added = 0
    for it in items:
        title = _clean(it.get("title"), 220)
        url = (it.get("url") or "").strip()
        published = it.get("published_at")
        if not title or not url.startswith("http") or not published:
            continue
        summary = _clean(it.get("summary"), 400)
        hits = safety.scan_injection(f"{title} {summary}")
        if hits:
            con.execute("INSERT INTO safety_events (ts, kind, detail) VALUES (?, ?, ?)",
                        (utcnow(), "injection_in_news", f"{it.get('source')}: {title[:120]} -> {hits}"))
        cur = con.execute(
            "INSERT OR IGNORE INTO headlines (source, title, summary, url, published_at, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (it.get("source", "unknown"), title, summary, url, published, utcnow()))
        added += cur.rowcount
    return added


def fetch_rss() -> list[dict]:
    import feedparser
    items: list[dict] = []
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
        except Exception as exc:  # one bad feed never stops the brief
            log.warning("RSS %s failed: %s", source, exc)
            continue
        for e in feed.entries:
            published = _to_utc_iso(e.get("published") or e.get("updated"))
            items.append({"source": source, "title": e.get("title"), "summary": e.get("summary"),
                          "url": e.get("link"), "published_at": published})
    return items


def refresh_rss(con: sqlite3.Connection) -> int:
    return store_headlines(con, fetch_rss())


GOOGLE_QUERIES = [
    "stock market",
    "fed OR inflation OR treasury yields OR jobs report",
    "earnings shares guidance",
    "oil OR tariff OR sanctions OR war markets",
    "nasdaq OR \"s&p 500\" OR dow",
]


def fetch_google_news(query: str, after: str | None = None, before: str | None = None,
                      when: str | None = None) -> list[dict]:
    """Google News RSS search (free, no key). after/before = YYYY-MM-DD for history; when = '1d' for live."""
    import urllib.parse

    import feedparser
    q = query
    if after:
        q += f" after:{after}"
    if before:
        q += f" before:{before}"
    if when:
        q += f" when:{when}"
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=en-US&gl=US&ceid=US:en"
    try:
        feed = feedparser.parse(url)
    except Exception as exc:
        log.warning("Google News failed for %r: %s", query, exc)
        return []
    out = []
    for e in feed.entries:
        src = (e.get("source") or {}).get("title") or "Google News"
        title = e.get("title") or ""
        if title.endswith(f" - {src}"):
            title = title[: -len(src) - 3]
        out.append({"source": src, "title": title, "summary": "", "url": e.get("link"),
                    "published_at": _to_utc_iso(e.get("published"))})
    return out


def refresh_google(con: sqlite3.Connection, after: str | None = None, before: str | None = None,
                   when: str | None = None) -> int:
    added = 0
    for q in GOOGLE_QUERIES:
        added += store_headlines(con, fetch_google_news(q, after, before, when))
        _time.sleep(0.5)
    return added


def refresh_live(con: sqlite3.Connection) -> int:
    """Everything a live poll collects: publisher RSS feeds plus a 1-day Google News sweep."""
    return refresh_rss(con) + refresh_google(con, when="1d")


# --- the pre-filter (cost lever: ~300 headlines -> ~40 before any model sees them) ---------

def _score(title: str, summary: str) -> int:
    text = f" {title} {summary} ".lower()
    if any(n in text for n in NOISE_TERMS):
        return -1
    score = 0
    for weight, terms in MARKET_TERMS.items():
        score += weight * sum(1 for t in terms if t in text)
    return score


def _tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9&]+", title.lower()) if len(w) > 2}


def candidates(con: sqlite3.Connection, start_iso: str, end_iso: str, limit: int = 40,
               prefilter: bool = True) -> list[dict]:
    """Headlines in [start, end), deduplicated and ranked. prefilter=False returns everything (A/B test)."""
    items = rows(con.execute(
        "SELECT id, source, title, summary, url, published_at FROM headlines "
        "WHERE published_at >= ? AND published_at < ? ORDER BY published_at DESC", (start_iso, end_iso)))
    # Quarantine: headlines that look like instructions never reach a model (logged at ingest).
    items = [it for it in items if not safety.scan_injection(f"{it['title']} {it.get('summary') or ''}")]
    if not prefilter:
        return items[:300]
    scored = []
    for it in items:
        s = _score(it["title"], it.get("summary") or "")
        if s > 0:
            scored.append((s, it))
    scored.sort(key=lambda x: -x[0])  # stable: ties stay newest-first
    kept: list[dict] = []
    seen: list[set[str]] = []
    for s, it in scored:
        tok = _tokens(it["title"])
        if any(len(tok & o) / max(1, len(tok | o)) > 0.5 for o in seen):
            continue  # near-duplicate story from another outlet
        seen.append(tok)
        kept.append({**it, "score": s})
        if len(kept) >= limit:
            break
    return kept


def window_for_brief(now_utc: datetime | None = None, hours: int = 24) -> tuple[str, str]:
    end = now_utc or datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")
