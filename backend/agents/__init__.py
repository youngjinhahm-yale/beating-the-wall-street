"""Builds the six agents. Each gets: the shared rules + its role prompt (static, so it is
prompt-cached), a typed output, and only the MCP tools on its allowlist (often none)."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass

from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.openai import OpenAIResponsesModelSettings

from .. import config
from . import models as m


@dataclass(frozen=True)
class Spec:
    prompt: str
    output: type
    tools: frozenset[str]


SPECS: dict[str, Spec] = {
    # Agents that read untrusted text get no tools at all, so an injected instruction has nothing to act with.
    "scout": Spec("scout.md", m.ScoutReport, frozenset({"search_news", "get_price_history", "get_market_snapshot"})),
    "editor": Spec("editor.md", m.EditorOutput, frozenset()),
    "factcheck": Spec("factcheck.md", m.FactCheckOutput, frozenset()),
    "analyst": Spec("analyst.md", m.Attribution, frozenset({"get_minute_bars", "search_news"})),
    "coach": Spec("coach_postmortem.md", m.PostMortem, frozenset({"get_price_history", "get_inflections"})),
    "coach_personal": Spec("coach_personal.md", m.CoachNote, frozenset()),
    "moderator": Spec("moderator.md", m.ModVerdict, frozenset()),
}

# Static instructions go first and get a cache breakpoint: repeat calls read them at the cached price.
SETTINGS = OpenAIResponsesModelSettings(openai_cache_instructions=True)


def _instructions(prompt_file: str) -> str:
    shared = (config.PROMPTS_DIR / "_shared.md").read_text(encoding="utf-8")
    role = (config.PROMPTS_DIR / prompt_file).read_text(encoding="utf-8")
    return f"{shared}\n\n{role}"


def build_mcp() -> MCPToolset:
    env = {**os.environ, "BTWS_DB_PATH": str(config.DB_PATH), "PYDANTIC_AI_NO_BANNER": "1"}
    transport = StdioTransport(command=sys.executable, args=[str(config.MCP_SERVER_SCRIPT)], env=env,
                               cwd=str(config.REPO_DIR))
    return MCPToolset(transport, id="btws-market", max_retries=2)


class Hub:
    """Holds the model, one MCP connection and the agents. Created lazily, reused for the process."""

    def __init__(self) -> None:
        self.model = config.build_model()
        self.mcp = build_mcp()
        self.agents = {name: self._build(spec) for name, spec in SPECS.items()}
        self._entered = False
        self._lock = asyncio.Lock()

    def _build(self, spec: Spec) -> Agent:
        toolsets = []
        if spec.tools:
            allowed = spec.tools
            toolsets.append(self.mcp.filtered(lambda ctx, tool_def, allowed=allowed: tool_def.name in allowed))
        return Agent(self.model, output_type=spec.output, instructions=_instructions(spec.prompt),
                     toolsets=toolsets, model_settings=SETTINGS, retries=2)

    async def ensure_mcp(self) -> None:
        async with self._lock:
            if not self._entered:
                await self.mcp.__aenter__()
                self._entered = True

    async def close(self) -> None:
        if self._entered:
            await self.mcp.__aexit__(None, None, None)
            self._entered = False


_hub: Hub | None = None


async def hub() -> Hub:
    global _hub
    if _hub is None:
        _hub = Hub()
    await _hub.ensure_mcp()
    return _hub


async def close_hub() -> None:
    global _hub
    if _hub is not None:
        await _hub.close()
        _hub = None
