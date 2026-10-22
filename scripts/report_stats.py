"""Collect every number the report quotes, straight from the live database and test outputs.

    python scripts/report_stats.py      ->  report/stats.json
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend.usage import summary  # noqa: E402


def main() -> None:
    con = sqlite3.connect(config.LIVE_DB)
    con.row_factory = sqlite3.Row
    q = lambda sql, *a: [dict(r) for r in con.execute(sql, a)]  # noqa: E731

    runs = q("SELECT id, kind, status, trace FROM pipeline_runs WHERE status = 'ok'")
    fc_pass = fc_fail = 0
    retries = 0
    headline_in, headline_kept = [], []
    for r in runs:
        for st in json.loads(r["trace"] or "[]"):
            if st["step"].startswith("factcheck"):
                fc_pass += st["detail"].get("passed", 0)
                fc_fail += st["detail"].get("failed", 0)
            if st["step"] == "editor_retry":
                retries += 1
            if st["step"] == "news_prefilter" and st["detail"].get("prefilter"):
                headline_in.append(st["detail"]["headlines_in_window"])
                headline_kept.append(st["detail"]["candidates_after_prefilter"])

    # One typical live morning run (the most recent manual/morning run) with per-step usage.
    typical = q("SELECT id, trace FROM pipeline_runs WHERE kind IN ('manual','morning') AND status = 'ok' "
                "ORDER BY started_at DESC LIMIT 1")
    typical_steps = []
    if typical:
        for st in json.loads(typical[0]["trace"]):
            typical_steps.append({"step": st["step"], "usage": st.get("usage"),
                                  "detail": {k: v for k, v in st["detail"].items() if k != "snapshot"}})

    evening = q("SELECT l.run_id, SUM(input_tokens) inp, SUM(cache_read_tokens) cached, SUM(output_tokens) out, "
                "SUM(cost_usd) cost FROM llm_calls l JOIN pipeline_runs p ON p.id = l.run_id "
                "WHERE p.kind = 'evening' AND p.status = 'ok' GROUP BY l.run_id")
    morning = q("SELECT l.run_id, SUM(input_tokens) inp, SUM(cache_read_tokens) cached, SUM(output_tokens) out, "
                "SUM(cost_usd) cost FROM llm_calls l JOIN pipeline_runs p ON p.id = l.run_id "
                "WHERE p.kind IN ('morning','manual','history') AND p.status = 'ok' GROUP BY l.run_id")

    def avg(rows, k):
        return round(sum(r[k] for r in rows) / len(rows), 6) if rows else 0

    cards = q("SELECT kind, category, outcome, base_rates, post_mortem FROM cards WHERE outcome IS NOT NULL")
    outcomes = Counter(c["outcome"] for c in cards if c["kind"] == "daily")
    exp = Counter()
    for c in cards:
        if c["kind"] == "daily" and c["outcome"] != "void":
            for k, v in json.loads(c["base_rates"]).items():
                exp[k] += v
    drivers = Counter((json.loads(c["post_mortem"]) or {}).get("driver") for c in cards
                      if c["kind"] == "daily" and c["post_mortem"])
    cats = Counter(c["category"] for c in cards if c["kind"] == "daily")
    infl = Counter(r["confidence"] for r in q("SELECT confidence FROM inflections"))

    out = {
        "usage": summary(),
        "rounds": q("SELECT COUNT(*) n, MIN(trade_date) first, MAX(trade_date) last FROM rounds")[0],
        "daily_cards_settled": sum(outcomes.values()),
        "outcomes": dict(outcomes),
        "expected_outcomes_from_base_rates": {k: round(v, 1) for k, v in exp.items()},
        "categories": dict(cats),
        "coach_driver": dict(drivers),
        "inflection_confidence": dict(infl),
        "inflection_cards": q("SELECT COUNT(*) n FROM cards WHERE kind = 'inflection'")[0]["n"],
        "headlines": q("SELECT COUNT(*) n FROM headlines")[0]["n"],
        "factcheck": {"passed": fc_pass, "failed": fc_fail, "editor_retries": retries},
        "prefilter_live_avg": {"headlines_in_window": round(sum(headline_in) / max(1, len(headline_in))),
                               "kept": round(sum(headline_kept) / max(1, len(headline_kept)))},
        "morning_avg": {k: avg(morning, k) for k in ("inp", "cached", "out", "cost")} | {"runs": len(morning)},
        "evening_avg": {k: avg(evening, k) for k in ("inp", "cached", "out", "cost")} | {"runs": len(evening)},
        "typical_morning_run": {"id": typical[0]["id"] if typical else None, "steps": typical_steps},
        "safety_events": dict(Counter(r["kind"] for r in q("SELECT kind FROM safety_events"))),
    }
    for name, rel in (("redteam", "tests/safety/redteam_results.json"), ("ab_prefilter", "scripts/ab_prefilter_results.json")):
        p = ROOT / rel
        out[name] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    dst = ROOT / "report" / "stats.json"
    dst.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"wrote {dst}")
    print(json.dumps({k: out[k] for k in ("rounds", "daily_cards_settled", "outcomes", "expected_outcomes_from_base_rates",
                                          "coach_driver", "inflection_confidence", "factcheck", "prefilter_live_avg",
                                          "morning_avg", "evening_avg", "safety_events")}, indent=1, default=str))


if __name__ == "__main__":
    main()
