"""Paths, keys, the one model, prices, and the limits that keep the game fair and cheap."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from dotenv import load_dotenv  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent
REPO_DIR = BACKEND_DIR.parent

# Course rule: look for .env in the repo folder first, then one folder up.
# override=False means the repo's own .env wins when both exist.
load_dotenv(REPO_DIR / ".env", override=False)
load_dotenv(REPO_DIR.parent / ".env", override=False)

DATA_DIR = REPO_DIR / "data"
PROMPTS_DIR = BACKEND_DIR / "agents" / "prompts"
FRONTEND_DIST = REPO_DIR / "frontend" / "dist"
MCP_SERVER_SCRIPT = REPO_DIR / "market_mcp" / "server.py"

# sample = ship-with-repo database (works offline); live = your own growing database.
DATA_MODE = os.getenv("DATA_MODE", "sample").strip().lower()
SAMPLE_DB = DATA_DIR / "sample.db"
LIVE_DB = DATA_DIR / "live.db"
DB_PATH = Path(os.getenv("BTWS_DB_PATH") or (LIVE_DB if DATA_MODE == "live" else SAMPLE_DB))

ET = ZoneInfo("America/New_York")

# --- Model ---------------------------------------------------------------------------
# The course key allows exactly one model. A personal OPENAI_API_KEY may pick another.
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-6-luna")

# USD per 1M tokens (OpenAI standard short-context prices, from Lecture 11).
PRICES = {
    "gpt-6-luna": {"input": 0.10, "cached": 0.01, "cache_write": 0.125, "output": 0.50},
}

# --- Budgets (overspending defense) ---------------------------------------------------
DAILY_TOKEN_CAP = int(os.getenv("DAILY_TOKEN_CAP", "600000"))       # all agents, per ET day
USER_DAILY_TOKEN_CAP = int(os.getenv("USER_DAILY_TOKEN_CAP", "40000"))  # coach + moderation per user
PIPELINE_COOLDOWN_MIN = int(os.getenv("PIPELINE_COOLDOWN_MIN", "30"))   # manual "run now" spacing
AGENT_REQUEST_LIMIT = 8        # model calls per agent run (stops loops)
AGENT_TOOL_CALL_LIMIT = 12
AGENT_TOTAL_TOKEN_LIMIT = 120_000

# --- Game rules ----------------------------------------------------------------------
START_POINTS = 1000
DAILY_ALLOWANCE = 100
MIN_STAKE, MAX_STAKE = 10, 100
LOCK_TIME = (9, 25)            # ET; bets close 5 minutes before the open
SETTLE_TIME = (16, 10)         # ET; after the 16:00 close
LP_K = 30                      # LP swing size
PRACTICE_LP_FACTOR = 1 / 3     # replay rounds count, but less than live ones
PLACEMENT_BETS = 5             # bets in a category before a tier is shown
START_LP = 100                 # Bronze after placement
HOUSE_SEED = 100               # points the house spreads across the 3 pools by base rate
MISSION_ALL_CARDS_BONUS = 50  # call every card in a round
FLAT_BAND_FACTOR = 0.30        # Flat band = factor x 20-day mean |open-to-close| move

CATEGORIES = ["macro", "company", "geopolitics", "sector"]
TIERS = [  # (name, LP needed)
    ("Iron", 0), ("Bronze", 100), ("Silver", 200), ("Gold", 300), ("Platinum", 400),
    ("Emerald", 500), ("Diamond", 600), ("Master", 700),
]
POST_THREAD_MIN_TIER = os.getenv("POST_THREAD_MIN_TIER", "Gold")  # start an insight thread in a category
VOTE_MIN_BETS = 5

# NYSE full-day closures we may hit while this app runs.
MARKET_HOLIDAYS = {
    "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15",
}

ADMIN_USERNAMES = {u.strip().lower() for u in os.getenv("ADMIN_USERNAMES", "admin").split(",") if u.strip()}


class AgentUnavailable(RuntimeError):
    """The model can't be reached (missing key, network, provider error)."""


def build_model():
    """gpt-6-luna through Portkey (course key), or a personal OpenAI key if that is all we have."""
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    portkey = os.getenv("PORTKEY_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if portkey:
        client = AsyncOpenAI(
            api_key=portkey,
            base_url=PORTKEY_BASE_URL,
            default_headers={
                "x-portkey-api-key": portkey,
                # Market news changes daily; never answer from Portkey's response cache.
                "x-portkey-cache-force-refresh": "true",
            },
        )
    elif openai_key:
        client = AsyncOpenAI(api_key=openai_key)
    else:
        raise AgentUnavailable("No PORTKEY_API_KEY or OPENAI_API_KEY found in .env (repo or parent folder).")
    return OpenAIResponsesModel(active_model_name(), provider=OpenAIProvider(openai_client=client))


def active_model_name() -> str:
    # On the course key only gpt-6-luna is allowed, whatever MODEL_NAME says.
    return "gpt-6-luna" if os.getenv("PORTKEY_API_KEY", "").strip() else MODEL_NAME
