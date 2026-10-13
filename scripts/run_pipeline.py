"""Run a pipeline from the command line.

    python scripts/run_pipeline.py morning [--force] [--no-prefilter]
    python scripts/run_pipeline.py evening [--date YYYY-MM-DD]
"""

from __future__ import annotations

import sys as _sys

for _s in (_sys.stdout, _sys.stderr):  # Windows consoles may not be UTF-8
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, pipeline  # noqa: E402
from backend.agents import close_hub  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["morning", "evening"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-prefilter", action="store_true")
    ap.add_argument("--date")
    a = ap.parse_args()
    db.init_db()
    try:
        if a.kind == "morning":
            out = await pipeline.run_morning(force=a.force, kind="manual", prefilter=not a.no_prefilter)
        else:
            out = await pipeline.run_evening(date.fromisoformat(a.date) if a.date else None)
        print(json.dumps(out, indent=2, default=str))
    finally:
        await close_hub()


if __name__ == "__main__":
    asyncio.run(main())
