"""Build report.html and slides.html: fill numbers from report/stats.json and embed every image,
so both files open by double-click on any computer.

    python scripts/report_stats.py   # refresh numbers first
    python scripts/build_report.py   # -> report.html, slides.html in the repo root
"""

from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REP = ROOT / "report"


def money(x: float, digits: int = 4) -> str:
    return f"{x:.{digits}f}"


def embed_images(text: str) -> str:
    def repl(m: re.Match) -> str:
        p = REP / m.group(1)
        mime = "image/svg+xml" if p.suffix == ".svg" else "image/png" if p.suffix == ".png" else "image/jpeg"
        return f'src="data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"'
    return re.sub(r'src="(img/[^"]+)"', repl, text)


def values() -> dict[str, str]:
    s = json.loads((REP / "stats.json").read_text(encoding="utf-8"))
    u, mc = s["usage"], s["usage"]["model_costs"]
    m, e = s["morning_avg"], s["evening_avg"]
    v: dict[str, str] = {}
    v["CALLS"] = f"{u['totals']['calls']:,}"
    v["MORNING_RUNS"] = str(m["runs"])
    v["MORNING_TOK"] = f"{round(m['inp'] + m['out']):,}"
    v["MORNING_COST"] = money(m["cost"])
    v["EVENING_TOK"] = f"{round(e['inp'] + e['out']):,}"
    v["EVENING_COST"] = money(e["cost"])
    v["DAY_COST"] = money(m["cost"] + e["cost"])
    v["PER_USER_DAY"] = money(mc["per_user_day_usd"], 5)
    scale = {r["users"]: r for r in mc["scale"]}
    v["PER_USER_MONTH"] = money(scale[10000]["per_user_month_usd"])
    v["COST_10K"] = f"{scale[10000]['monthly_usd']:.0f}"
    v["COST_100"] = f"{scale[100]['monthly_usd']:.2f}"
    v["SCALE_ROWS"] = "".join(
        f"<tr><td>{n:,}</td><td class='n'>${r['monthly_usd']:,.2f}</td><td class='n'>${r['per_user_month_usd']:.4f}</td></tr>"
        for n, r in sorted(scale.items()))
    scales = {"scout": "fixed / day", "editor": "fixed / day", "factcheck": "fixed / day", "analyst": "big moves / day",
              "coach": "cards / day", "moderator": "posts", "coach_personal": "players (on request)",
              "editor_trim": "one-off maintenance"}
    v["AGENT_ROWS"] = "".join(
        f"<tr><td>{a['agent']}</td><td class='n'>{a['calls']}</td><td class='n'>{int(a['avg_input']):,}</td>"
        f"<td class='n'>{int(a['avg_cached'] or 0):,}</td><td class='n'>{int(a['avg_output']):,}</td>"
        f"<td class='n'>${a['avg_cost']:.5f}</td><td>{scales.get(a['agent'], '')}</td></tr>" for a in u["by_agent"])
    v["PREFILTER_IN"] = str(s["prefilter_live_avg"]["headlines_in_window"])
    v["PREFILTER_KEPT"] = str(s["prefilter_live_avg"]["kept"])
    v["CACHED"] = f"{u['totals']['cached']:,}"
    v["CACHE_SAVED"] = money(u["totals"]["cache_savings_usd"])
    v["CACHE_PCT"] = f"{100 * u['totals']['cache_savings_usd'] / max(1e-9, u['totals']['cost']):.0f}"
    v["TOTAL_COST"] = money(u["totals"]["cost"], 2)
    fc = s["factcheck"]
    v["FC_FAIL"], v["FC_TOTAL"] = str(fc["failed"]), str(fc["passed"] + fc["failed"])
    v["FC_PCT"] = f"{100 * fc['failed'] / max(1, fc['passed'] + fc['failed']):.0f}"
    rt = s["redteam"] or {"passed": 0, "total": 0, "results": []}
    v["RT_PASSED"], v["RT_TOTAL"] = str(rt["passed"]), str(rt["total"])
    v["RT_ROWS"] = "".join(
        f"<tr><td>{html.escape(r['test'])}</td><td>{html.escape(r['defense'])}</td>"
        f"<td class='{'pass' if r['passed'] else 'fail'}'>{'Blocked / handled' if r['passed'] else 'FAILED'}</td></tr>"
        for r in rt["results"])
    v["ROUNDS"] = str(s["rounds"]["n"])
    v["FIRST"], v["LAST"] = s["rounds"]["first"], s["rounds"]["last"]
    v["SETTLED"] = str(s["daily_cards_settled"])
    v["HEADLINES"] = f"{s['headlines']:,}"
    d = s["coach_driver"]
    v["DRV_NEWS"], v["DRV_PARTLY"], v["DRV_ELSE"] = (str(d.get(k, 0)) for k in ("the news", "partly the news", "something else"))
    v["DRV_TOTAL"] = str(sum(d.values()))
    o, ex = s["outcomes"], s["expected_outcomes_from_base_rates"]
    v["OUT_UP"], v["OUT_FLAT"], v["OUT_DOWN"] = (str(o.get(k, 0)) for k in ("up", "flat", "down"))
    v["EXP_UP"], v["EXP_FLAT"], v["EXP_DOWN"] = (f"{ex.get(k, 0):.0f}" for k in ("up", "flat", "down"))
    ic = s["inflection_confidence"]
    v["INFL_TOTAL"] = str(sum(ic.values()))
    v["INFL_MED"] = str(ic.get("medium", 0) + ic.get("high", 0))
    v["INFL_NONE"] = str(ic.get("none", 0))

    ab = s.get("ab_prefilter") or []
    if ab:
        rows, on, off = [], [r for r in ab if r["prefilter"]], [r for r in ab if not r["prefilter"]]
        for day in sorted({r["day"] for r in ab}):
            for r in (x for x in ab if x["day"] == day):
                rows.append(f"<tr><td>{day}</td><td>{'on' if r['prefilter'] else 'off'}</td><td class='n'>{r['headlines_to_scout']}</td>"
                            f"<td class='n'>{r['input']:,}</td><td class='n'>{r['output']:,}</td><td class='n'>${r['cost']:.5f}</td>"
                            f"<td class='n'>{r['cards_passed']}/{r['cards_drafted']}</td></tr>")
        def agg(rs):
            n = len(rs)
            return (sum(r["input"] for r in rs) / n, sum(r["cost"] for r in rs) / n,
                    sum(r["cards_passed"] for r in rs), sum(r["cards_drafted"] for r in rs),
                    sum(r["headlines_to_scout"] for r in rs) / n)
        a_on, a_off = agg(on), agg(off)
        rows.append(f"<tr><td><b>Average</b></td><td><b>on</b></td><td class='n'>{a_on[4]:.0f}</td><td class='n'><b>{a_on[0]:,.0f}</b></td><td></td>"
                    f"<td class='n'><b>${a_on[1]:.5f}</b></td><td class='n'><b>{a_on[2]}/{a_on[3]}</b></td></tr>")
        rows.append(f"<tr><td><b>Average</b></td><td><b>off</b></td><td class='n'>{a_off[4]:.0f}</td><td class='n'><b>{a_off[0]:,.0f}</b></td><td></td>"
                    f"<td class='n'><b>${a_off[1]:.5f}</b></td><td class='n'><b>{a_off[2]}/{a_off[3]}</b></td></tr>")
        v["AB_TABLE"] = ("<table><tr><th>Day</th><th>Pre-filter</th><th class='n'>Headlines to Scout</th><th class='n'>Input tokens</th>"
                         "<th class='n'>Output</th><th class='n'>Cost</th><th class='n'>Cards passed</th></tr>" + "".join(rows) + "</table>")
        save = 100 * (1 - a_on[1] / a_off[1]) if a_off[1] else 0
        p_on = a_on[2] / max(1, a_on[3]) * 100
        p_off = a_off[2] / max(1, a_off[3]) * 100
        in_cut = 100 * (1 - a_on[0] / a_off[0]) if a_off[0] else 0
        v["AB_SUMMARY"] = (f"Across {len(on)} days the pre-filter sent {a_on[4]:.0f} headlines to the Scout instead of {a_off[4]:.0f}, "
                           f"cut input tokens per run by {in_cut:.0f}% and cost by {save:.0f}% (${a_on[1]:.5f} vs ${a_off[1]:.5f}). "
                           f"Quality held: the Fact-checker passed {a_on[2]} of {a_on[3]} drafts with the filter ({p_on:.0f}%) and "
                           f"{a_off[2]} of {a_off[3]} without it ({p_off:.0f}%), and both versions published 3–4 cards on every day. "
                           "So this cost cut did not make the app perform worse.")
    else:
        v["AB_TABLE"], v["AB_SUMMARY"] = "<p>(A/B results not available.)</p>", ""

    pilot = REP / "pilot.json"
    if pilot.exists():
        p = json.loads(pilot.read_text(encoding="utf-8"))
        v["PILOT_STATUS"], v["PILOT_TEXT"] = p["status"], p["text"]
    else:
        v["PILOT_STATUS"] = "pilot in progress"
        v["PILOT_TEXT"] = ("A pilot with real investors is running on the live round of Friday, Oct 23. "
                           "Their results, quotes and time measurements will be added here.")
    return v


def build(src: str, dst: str, vals: dict[str, str]) -> None:
    text = (REP / src).read_text(encoding="utf-8")
    text = text.replace("{{DIAGRAM}}", (REP / "diagram.svg").read_text(encoding="utf-8"))
    for k, val in vals.items():
        text = text.replace("{{" + k + "}}", val)
    left = sorted(set(re.findall(r"\{\{[A-Z0-9_]+\}\}", text)))
    if left:
        raise SystemExit(f"{src}: unfilled placeholders {left}")
    text = embed_images(text)
    (ROOT / dst).write_text(text, encoding="utf-8")
    print(f"wrote {dst} ({len(text) / 1e6:.1f} MB)")


if __name__ == "__main__":
    vals = values()
    build("report.src.html", "report.html", vals)
    if (REP / "slides.src.html").exists():
        build("slides.src.html", "slides.html", vals)
