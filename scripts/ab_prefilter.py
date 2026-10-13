"""A/B test for the main cost lever: does the headline pre-filter save tokens without hurting card quality?

Runs the real morning pipeline twice for each past day on a scratch copy of the database:
  A = pre-filter on (top ~40 de-duplicated, scored headlines)   B = pre-filter off (every headline in the window, max 300)
and compares tokens, cost, Fact-checker pass rate and which events were picked.

    python scripts/ab_prefilter.py 2026-10-08 2026-10-09 2026-10-12
"""

from __future__ import annotations

import sys as _sys

for _s in (_sys.stdout, _sys.stderr):  # Windows consoles may not be UTF-8
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHILD = r"""
import asyncio, json, sys
from datetime import datetime, time
sys.path.insert(0, sys.argv[1])
from backend import config, pipeline
from backend.agents import close_hub
from backend.db import connect, one, rows

async def go(day, prefilter):
    with connect() as con:
        r = one(con.execute("SELECT id FROM rounds WHERE trade_date = ?", (day,)))
        if r:
            pipeline._delete_round(con, r["id"])
    as_of = datetime.combine(datetime.fromisoformat(day).date(), time(6, 0), tzinfo=config.ET)
    try:
        out = await pipeline.run_morning(as_of, kind="ab_" + ("on" if prefilter else "off"), prefilter=prefilter)
    finally:
        await close_hub()
    with connect() as con:
        calls = rows(con.execute("SELECT agent, input_tokens, cache_read_tokens, output_tokens, cost_usd FROM llm_calls WHERE run_id = ?", (out["run_id"],)))
        trace = json.loads(one(con.execute("SELECT trace FROM pipeline_runs WHERE id = ?", (out["run_id"],)))["trace"])
    fc = [s for s in trace if s["step"].startswith("factcheck")]
    pre = next(s for s in trace if s["step"] == "news_prefilter")["detail"]
    scout = next(s for s in trace if s["step"] == "scout")["detail"]
    print(json.dumps({"day": day, "prefilter": prefilter, "headlines_to_scout": pre["candidates_after_prefilter"],
        "input": sum(c["input_tokens"] for c in calls), "cached": sum(c["cache_read_tokens"] for c in calls),
        "output": sum(c["output_tokens"] for c in calls), "cost": sum(c["cost_usd"] for c in calls),
        "cards_drafted": sum(s["detail"]["passed"] + s["detail"]["failed"] for s in fc),
        "cards_passed": sum(s["detail"]["passed"] for s in fc), "published": [c["title"] for c in out.get("cards", [])],
        "scout_topics": [e["topic"] for e in scout["events"]]}))

asyncio.run(go(sys.argv[2], sys.argv[3] == "on"))
"""


def main() -> None:
    days = sys.argv[1:]
    if not days:
        sys.exit(__doc__)
    results = []
    with tempfile.TemporaryDirectory() as d:
        for day in days:
            for mode in ("on", "off"):
                scratch = Path(d) / f"ab_{day}_{mode}.db"
                shutil.copyfile(ROOT / "data" / "live.db", scratch)
                env = {**os.environ, "BTWS_DB_PATH": str(scratch), "PYDANTIC_AI_NO_BANNER": "1"}
                p = subprocess.run([sys.executable, "-c", CHILD, str(ROOT), day, mode], env=env, capture_output=True,
                                   text=True, encoding="utf-8")
                line = next((ln for ln in reversed(p.stdout.splitlines()) if ln.startswith("{")), None)
                if not line:
                    print(f"{day} {mode}: failed\n{p.stderr[-800:]}")
                    continue
                r = json.loads(line)
                results.append(r)
                print(f"{day} prefilter={mode}: {r['headlines_to_scout']} headlines, {r['input']:,} in / {r['output']:,} out, "
                      f"${r['cost']:.5f}, passed {r['cards_passed']}/{r['cards_drafted']}")
    out = ROOT / "scripts" / "ab_prefilter_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    for mode in (True, False):
        rs = [r for r in results if r["prefilter"] is mode]
        if rs:
            n = len(rs)
            print(f"\nprefilter={'on' if mode else 'off'}: avg input {sum(r['input'] for r in rs) / n:,.0f}, "
                  f"avg cost ${sum(r['cost'] for r in rs) / n:.5f}, "
                  f"pass rate {sum(r['cards_passed'] for r in rs)}/{sum(r['cards_drafted'] for r in rs)}")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
