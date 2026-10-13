"""Shorten cards written before the concise-card rules (title <= 60 chars, one summary sentence <= 18 words).

Only rewrites wording; facts must stay the same. Every rewrite is logged in llm_calls as 'editor_trim'.

    python scripts/tighten_cards.py
"""

from __future__ import annotations

import sys as _sys

for _s in (_sys.stdout, _sys.stderr):  # Windows consoles may not be UTF-8
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, Field  # noqa: E402
from pydantic_ai import Agent  # noqa: E402

from backend import config, safety  # noqa: E402
from backend.agents import SETTINGS  # noqa: E402
from backend.db import connect, rows  # noqa: E402
from backend.llm import run_agent  # noqa: E402


class Tight(BaseModel):
    title: str = Field(description="Max 60 characters, neutral, no direction words")
    summary: str = Field(description="ONE sentence, max 18 words, keeping the key fact or number")


INSTRUCTIONS = (
    (config.PROMPTS_DIR / "_shared.md").read_text(encoding="utf-8")
    + "\n\n# Role: Copy editor\nShorten a betting card. Keep every fact exactly as given; add nothing. "
    "title: max 60 characters. summary: one sentence, max 18 words. No direction words, no background."
)


def too_long(c: dict) -> bool:
    return len(c["title"]) > 60 or len(c["summary"].split()) > 20 or c["summary"].count(". ") >= 1


async def main() -> None:
    agent = Agent(config.build_model(), output_type=Tight, instructions=INSTRUCTIONS, model_settings=SETTINGS)
    with connect() as con:
        cards = [c for c in rows(con.execute("SELECT id, title, summary FROM cards WHERE kind = 'daily'")) if too_long(c)]
    print(f"{len(cards)} cards to tighten")
    for c in cards:
        out, _ = await run_agent(agent, "editor_trim", safety.quote_untrusted("CARD", [c], ["title", "summary"]),
                                 run_id="tighten")
        if safety.directional_hits(f"{out.title} {out.summary}") or len(out.title) > 70:
            print(f"  skipped {c['id']}: {out.title}")
            continue
        with connect() as con:
            con.execute("UPDATE cards SET title = ?, summary = ? WHERE id = ?", (out.title, out.summary, c["id"]))
        print(f"  {c['id']}: {out.title} | {out.summary}")


if __name__ == "__main__":
    asyncio.run(main())
