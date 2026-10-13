"""Copy data/live.db to data/sample.db without any player data.

Keeps: rounds, cards, prices, minute bars (indices), headlines, inflections, pipeline traces and the measured
llm_calls usage log (evidence for the Under the Hood page). Removes: users, sessions, bets, rules, posts, votes.

    python scripts/make_sample_db.py
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402


def main() -> None:
    src, dst = config.LIVE_DB, config.SAMPLE_DB
    if not src.exists():
        sys.exit(f"{src} not found")
    tmp = dst.with_suffix(".tmp")
    shutil.copyfile(src, tmp)
    con = sqlite3.connect(tmp)
    for t in ("votes", "reports", "posts", "bets", "rules", "sessions", "category_ratings", "users"):
        con.execute(f"DELETE FROM {t}")
    con.execute("UPDATE llm_calls SET user_id = NULL")
    con.execute("UPDATE safety_events SET user_id = NULL")
    con.commit()
    con.execute("VACUUM")
    con.close()
    tmp.replace(dst)
    print(f"wrote {dst} ({dst.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
