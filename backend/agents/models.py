"""Structured outputs: every agent returns one of these typed objects, never free text."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["macro", "company", "geopolitics", "sector"]


class CandidateEvent(BaseModel):
    topic: str = Field(description="Short neutral name of the event, e.g. 'September CPI release'")
    headline_ids: list[int] = Field(description="ids of the headlines that report this event", min_length=1)
    category: Category
    why_market_moving: str = Field(description="One sentence: why U.S. markets could react (no direction)")
    suggested_ticker: str = Field(description="Most affected index, sector ETF or U.S. stock ticker, e.g. ^GSPC, XLE, NVDA")
    importance: int = Field(ge=1, le=5, description="5 = likely to move the whole market")


class ScoutReport(BaseModel):
    events: list[CandidateEvent] = Field(max_length=8)
    market_backdrop: str = Field(description="Two neutral sentences on overnight futures and key indicators")


class CardDraft(BaseModel):
    title: str = Field(description="Neutral headline, max 60 characters, no direction words")
    summary: str = Field(description="ONE short neutral sentence (max 18 words) with the key fact or number, no prediction")
    category: Category
    ticker: str = Field(description="Target to bet on: index (^GSPC, ^IXIC, ^DJI), sector ETF, or U.S. stock ticker")
    headline_ids: list[int] = Field(min_length=1)


class EditorOutput(BaseModel):
    brief: str = Field(description="One short neutral sentence (max 15 words) naming today's themes")
    cards: list[CardDraft] = Field(min_length=1, max_length=5)


class CardVerdict(BaseModel):
    index: int = Field(description="0-based position of the card in the list")
    passed: bool
    problems: list[str] = Field(default_factory=list, description="Each unsupported claim or directional hint found")


class FactCheckOutput(BaseModel):
    verdicts: list[CardVerdict]


class Attribution(BaseModel):
    headline_id: int | None = Field(description="id of the headline that most likely caused the move, or null")
    category: Category = Field(description="Category of the causing news (macro if no clear cause)")
    cause: str = Field(description="One sentence naming the likely cause, or 'No clear news cause'")
    mechanism: str = Field(description="One sentence on how that news would move this index")
    confidence: Literal["high", "medium", "low", "none"]


class PostMortem(BaseModel):
    what_happened: str = Field(description="One sentence with the actual move, using the numbers given")
    why: str = Field(description="At most two short sentences on the most likely drivers")
    driver: Literal["the news", "partly the news", "something else"] = Field(
        description="Did this card's news drive the move?")
    lesson_rule: str = Field(description="One testable if-then rule a player can reuse, max 90 characters")


class CoachNote(BaseModel):
    feedback: str = Field(description="Max two short sentences on the player's reasoning versus what happened")
    suggested_rule: str = Field(description="One testable if-then rule, max 90 characters")


class ModVerdict(BaseModel):
    decision: Literal["approve", "reject", "flag"]
    reason: str = Field(description="One short sentence shown to the author")
