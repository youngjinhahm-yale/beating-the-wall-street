"""FastAPI app: the game API plus the built React site, served from one process."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, community, config, game, market, pipeline, scheduler, usage
from .agents import close_hub
from .config import AgentUnavailable
from .db import connect, init_db, loads, one, rows
from .llm import BudgetExceeded, tokens_used_today

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("btws")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    sched = scheduler.build()
    sched.start()
    log.info("DB %s | mode %s | scheduler %s", config.DB_PATH, config.DATA_MODE,
             "on" if scheduler.scheduler_enabled() else "lock/settle only")
    yield
    sched.shutdown(wait=False)
    await close_hub()


app = FastAPI(title="Beating the Wall Street", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(game.GameError)
async def game_error(_: Request, exc: game.GameError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(BudgetExceeded)
async def budget_error(_: Request, exc: BudgetExceeded):
    return JSONResponse({"detail": str(exc)}, status_code=429)


@app.exception_handler(AgentUnavailable)
async def agent_error(_: Request, exc: AgentUnavailable):
    return JSONResponse({"detail": str(exc)}, status_code=503)


# --- auth ----------------------------------------------------------------------------

class Credentials(BaseModel):
    username: str
    pin: str


@app.post("/api/register")
def register(c: Credentials):
    return auth.register(c.username, c.pin)


@app.post("/api/login")
def login(c: Credentials):
    return auth.login(c.username, c.pin)


@app.post("/api/logout")
def logout(request: Request):
    tok = auth._token(request.headers.get("authorization"))
    if tok:
        auth.logout(tok)
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(auth.current_user)):
    with connect() as con:
        return game.profile(con, user["id"])


# --- game ----------------------------------------------------------------------------

def _round_payload(con, rnd: dict, user_id: int | None) -> dict:
    cards = rows(con.execute("SELECT * FROM cards WHERE round_id = ? ORDER BY id", (rnd["id"],)))
    return {"id": rnd["id"], "trade_date": rnd["trade_date"], "status": rnd["status"], "lock_at": rnd["lock_at"],
            "brief": rnd["brief"], "snapshot": loads(rnd.get("snapshot"), []),
            "cards": [game.card_public(con, c, user_id) for c in cards]}


@app.get("/api/today")
def today(user=Depends(auth.optional_user)):
    uid = user["id"] if user else None
    with connect() as con:
        game.lock_due_rounds(con)
        rnd = (one(con.execute("SELECT * FROM rounds WHERE status = 'open' ORDER BY trade_date LIMIT 1"))
               or one(con.execute("SELECT * FROM rounds WHERE status = 'locked' ORDER BY trade_date DESC LIMIT 1"))
               or one(con.execute("SELECT * FROM rounds ORDER BY trade_date DESC LIMIT 1")))
        nxt = market.target_trade_date()
        return {"server_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "next_trade_date": nxt.isoformat(),
                "next_lock_at": market.lock_dt(nxt).astimezone(timezone.utc).isoformat(timespec="seconds"),
                "round": _round_payload(con, rnd, uid) if rnd else None}


@app.get("/api/rounds")
def list_rounds():
    with connect() as con:
        return rows(con.execute(
            "SELECT r.id, r.trade_date, r.status, COUNT(c.id) cards FROM rounds r LEFT JOIN cards c ON c.round_id = r.id "
            "GROUP BY r.id ORDER BY r.trade_date DESC LIMIT 60"))


@app.get("/api/rounds/{round_id}")
def get_round(round_id: int, user=Depends(auth.optional_user)):
    with connect() as con:
        rnd = one(con.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)))
        if not rnd:
            raise HTTPException(404, "Round not found.")
        return _round_payload(con, rnd, user["id"] if user else None)


@app.get("/api/cards/{card_id}")
def get_card(card_id: int, user=Depends(auth.optional_user)):
    with connect() as con:
        card = one(con.execute("SELECT * FROM cards WHERE id = ?", (card_id,)))
        if not card:
            raise HTTPException(404, "Card not found.")
        return game.card_public(con, card, user["id"] if user else None)


class BetIn(BaseModel):
    card_id: int
    direction: str
    stake: int = 0
    reason: str
    rule_id: int | None = None


@app.post("/api/bets")
def place_bet(b: BetIn, user=Depends(auth.current_user)):
    with connect() as con:
        return game.place_bet(con, user["id"], b.card_id, b.direction, b.stake, b.reason, b.rule_id)


@app.get("/api/bets/mine")
def my_bets(user=Depends(auth.current_user)):
    with connect() as con:
        out = rows(con.execute(
            "SELECT b.*, c.title, c.category, c.ticker, c.kind, c.outcome, c.move_pct, c.flat_band, c.lesson_rule, "
            "r.trade_date, ru.text AS rule_text FROM bets b JOIN cards c ON c.id = b.card_id "
            "LEFT JOIN rounds r ON r.id = c.round_id LEFT JOIN rules ru ON ru.id = b.rule_id "
            "WHERE b.user_id = ? ORDER BY b.created_at DESC LIMIT 200", (user["id"],)))
    for b in out:
        b["coach_note"] = loads(b["coach_note"], None)
    return out


@app.post("/api/bets/{bet_id}/coach")
async def coach(bet_id: int, user=Depends(auth.current_user)):
    return await pipeline.coach_bet(bet_id, user["id"])


@app.get("/api/practice")
def practice(user=Depends(auth.current_user)):
    with connect() as con:
        cards = rows(con.execute(
            "SELECT c.* FROM cards c WHERE c.outcome IS NOT NULL AND c.outcome != 'void' AND NOT EXISTS "
            "(SELECT 1 FROM bets b WHERE b.card_id = c.id AND b.user_id = ?) ORDER BY c.created_at DESC LIMIT 60",
            (user["id"],)))
        done = con.execute("SELECT COUNT(*) FROM bets WHERE user_id = ? AND practice = 1", (user["id"],)).fetchone()[0]
        return {"done": done, "cards": [game.card_public(con, c, user["id"]) for c in cards]}


# --- rules, leaderboard --------------------------------------------------------------

class RuleIn(BaseModel):
    text: str
    category: str
    source: str = "user"


@app.get("/api/rules")
def get_rules(user=Depends(auth.current_user)):
    with connect() as con:
        return game.rulebook(con, user["id"])


@app.post("/api/rules")
def add_rule(r: RuleIn, user=Depends(auth.current_user)):
    with connect() as con:
        return game.add_rule(con, user["id"], r.text, r.category, "coach" if r.source == "coach" else "user")


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int, user=Depends(auth.current_user)):
    with connect() as con:
        con.execute("UPDATE bets SET rule_id = NULL WHERE rule_id = ? AND user_id = ?", (rule_id, user["id"]))
        con.execute("DELETE FROM rules WHERE id = ? AND user_id = ?", (rule_id, user["id"]))
    return {"ok": True}


@app.get("/api/leaderboard")
def leaderboard(category: str | None = None):
    if category and category not in config.CATEGORIES:
        raise HTTPException(400, "Unknown category.")
    with connect() as con:
        return game.leaderboard(con, category)


@app.get("/api/meta")
def meta():
    return {"categories": config.CATEGORIES, "tiers": config.TIERS + [("Grandmaster", None), ("Challenger", None)],
            "post_thread_min_tier": config.POST_THREAD_MIN_TIER, "min_stake": config.MIN_STAKE,
            "max_stake": config.MAX_STAKE, "placement_bets": config.PLACEMENT_BETS, "lp_k": config.LP_K,
            "data_mode": config.DATA_MODE, "model": config.active_model_name()}


# --- community -----------------------------------------------------------------------

class PostIn(BaseModel):
    body: str = Field(max_length=6000)
    title: str | None = Field(default=None, max_length=200)
    card_id: int | None = None
    category: str | None = None
    parent_id: int | None = None


@app.get("/api/posts")
def list_posts(category: str | None = None, card_id: int | None = None, user=Depends(auth.optional_user)):
    return community.list_threads(user["id"] if user else None, category, card_id)


@app.get("/api/posts/{post_id}")
def get_post(post_id: int, user=Depends(auth.optional_user)):
    t = community.thread(user["id"] if user else None, post_id)
    if not t:
        raise HTTPException(404, "Post not found.")
    return t


@app.post("/api/posts")
async def create_post(p: PostIn, user=Depends(auth.current_user)):
    return await community.create_post(user, body=p.body, title=p.title, card_id=p.card_id, category=p.category,
                                       parent_id=p.parent_id)


class VoteIn(BaseModel):
    value: int


@app.post("/api/posts/{post_id}/vote")
def vote(post_id: int, v: VoteIn, user=Depends(auth.current_user)):
    return community.vote(user, post_id, v.value)


@app.post("/api/posts/{post_id}/report")
def report(post_id: int, user=Depends(auth.current_user)):
    return community.report(user, post_id)


# --- pipelines, usage, safety --------------------------------------------------------

def _cooldown_check(kind: str) -> None:
    with connect() as con:
        last = con.execute("SELECT started_at FROM pipeline_runs WHERE kind = ? ORDER BY started_at DESC LIMIT 1",
                           (kind,)).fetchone()
    if last and datetime.now(timezone.utc) - datetime.fromisoformat(last[0]) < timedelta(
            minutes=config.PIPELINE_COOLDOWN_MIN):
        raise HTTPException(429, f"A {kind} run started less than {config.PIPELINE_COOLDOWN_MIN} minutes ago. "
                                 "Please wait (this protects the token budget).")


@app.post("/api/pipeline/morning")
async def run_morning(force: bool = False, user=Depends(auth.current_user)):
    if force and not user["is_admin"]:
        raise HTTPException(403, "Only an admin can rebuild an existing round.")
    _cooldown_check("manual")
    try:
        return await pipeline.run_morning(force=force, kind="manual")
    except pipeline.PipelineError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/pipeline/evening")
async def run_evening(user=Depends(auth.current_user)):
    _cooldown_check("evening")
    return await pipeline.run_evening()


@app.get("/api/pipeline/runs")
def runs():
    return pipeline.recent_runs()


@app.get("/api/pipeline/runs/{run_id}")
def run_detail(run_id: str):
    r = pipeline.run_detail(run_id)
    if not r:
        raise HTTPException(404, "Run not found.")
    return r


@app.get("/api/usage")
def get_usage():
    return usage.summary()


@app.get("/api/safety/events")
def safety_events(limit: int = 50):
    with connect() as con:
        return rows(con.execute("SELECT * FROM safety_events ORDER BY id DESC LIMIT ?", (min(limit, 200),)))


@app.get("/api/health")
def health():
    import os
    return {"ok": True, "data_mode": config.DATA_MODE, "db": config.DB_PATH.name,
            "model": config.active_model_name(),
            "key_found": bool(os.getenv("PORTKEY_API_KEY") or os.getenv("OPENAI_API_KEY")),
            "tokens_today": tokens_used_today(), "daily_cap": config.DAILY_TOKEN_CAP,
            "scheduler": scheduler.scheduler_enabled()}


# --- the React site ------------------------------------------------------------------

if (config.FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found.")
    target = config.FRONTEND_DIST / path
    if path and target.is_file() and config.FRONTEND_DIST in target.resolve().parents:
        return FileResponse(target)
    index = config.FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})  # always pick up a new build
    return JSONResponse({"detail": "Frontend not built. Run: cd frontend && npm install && npm run build"}, 404)
