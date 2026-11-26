"""Run all 48 incidents through a triager and score it against the labels.

    python -m triage.evaluate --triager runbook
    python -m triage.evaluate --triager agent --mode replay    # CI, no key
    python -m triage.evaluate --triager agent --mode record    # real run, needs ANTHROPIC_API_KEY

Scores
  diagnosis accuracy     category, root-cause table and action all right
  real problems closed   a real or upstream break proposed as "close" (the worst outcome)
  bad reloads            an upstream break proposed as "reload" (copies bad data again)
  pages to people        how many incidents end with a person being asked to act
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from .agent import investigate
from .incidents import Incident, all_incidents, materialise
from .llm import LLM, MissingRecording
from .policy import decide
from .warehouse import evaluate_check

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "report"


def runbook(con, inc: Incident, alert: dict) -> dict:
    """What a typical scripted runbook does, with no judgement."""
    _, passes_now = evaluate_check(con, inc.check_id, inc.now, inc.day)
    if passes_now:
        return {"category": "late_data", "root_cause_table": alert["table"], "proposed_action": "close",
                "evidence": "check passes on rerun", "owner_note": ""}
    status = con.execute("SELECT status FROM meta.pipeline_runs WHERE table_name = ? "
                         "ORDER BY started_at DESC LIMIT 1", [alert["table"]]).fetchone()
    if status and status[0] == "failed":
        return {"category": "real_break", "root_cause_table": alert["table"], "proposed_action": "reload",
                "evidence": "latest run failed", "owner_note": ""}
    return {"category": "upstream_break", "root_cause_table": alert["table"], "proposed_action": "escalate",
            "evidence": "check still failing, runs look normal", "owner_note": ""}


@dataclass
class Row:
    incident_id: str
    template: str
    table: str
    truth: str
    truth_root: str
    truth_action: str
    category: str
    root_cause_table: str
    proposed_action: str
    executed: str
    needs_person: bool
    correct: bool
    category_ok: bool
    root_ok: bool
    action_ok: bool
    finished: bool = True
    turns: int = 0
    tool_calls: int = 0
    evidence: str = ""
    owner_note: str = ""


def score(inc: Incident, diag: dict | None, tier_of, owner_of) -> Row:
    if diag is None:  # agent didn't finish: escalate, a person looks
        diag = {"category": "unknown", "root_cause_table": inc.table, "proposed_action": "escalate",
                "evidence": "", "owner_note": ""}
    root = diag["root_cause_table"]
    d = decide(diag["proposed_action"], tier_of.get(root, 1), owner_of.get(root, "data-platform"))
    cat_ok = diag["category"] == inc.category
    root_ok = root == inc.root_cause_table
    act_ok = diag["proposed_action"] == inc.action
    return Row(inc.incident_id, inc.template, inc.table, inc.category, inc.root_cause_table, inc.action,
               diag["category"], root, diag["proposed_action"], d.executed, d.needs_person,
               cat_ok and root_ok and act_ok, cat_ok, root_ok, act_ok,
               evidence=diag.get("evidence", ""), owner_note=diag.get("owner_note", ""))


def run(triager: str, mode: str, limit: int | None) -> tuple[list[Row], dict]:
    incidents = all_incidents()[:limit] if limit else all_incidents()
    llm = LLM(mode, ROOT / "recordings") if triager == "agent" else None
    rows: list[Row] = []
    for inc in incidents:
        con, alert = materialise(inc)
        meta = con.execute("SELECT table_name, tier, owner FROM meta.tables").fetchall()
        tier_of = {t: tier for t, tier, _ in meta}
        owner_of = {t: o for t, _, o in meta}
        if triager == "runbook":
            row = score(inc, runbook(con, inc, alert), tier_of, owner_of)
        else:
            try:
                trace = investigate(llm, con, alert)
            except MissingRecording:
                print(f"{inc.incident_id}: no recording, skipped")
                con.close()
                continue
            row = score(inc, trace.diagnosis, tier_of, owner_of)
            row.finished, row.turns, row.tool_calls = trace.finished, trace.turns, trace.tool_calls
        rows.append(row)
        con.close()
        print(f"{row.incident_id} {row.template:18} truth={row.truth:14} got={row.category:14} "
              f"{'OK ' if row.correct else 'XX '} -> {row.executed}")
    usage = None
    if llm:
        usage = {"model": llm.model, "effort": llm.effort, "mode": mode, **llm.usage.to_dict(),
                 "cost_usd": llm.usage.cost_usd(llm.model)}
    return rows, usage


def summarise(rows: list[Row], triager: str, usage: dict | None) -> dict:
    n = len(rows)
    problems = {"real_break", "upstream_break"}
    s = {
        "triager": triager, "incidents": n,
        "diagnosis_correct": sum(r.correct for r in rows),
        "category_correct": sum(r.category_ok for r in rows),
        "root_cause_correct": sum(r.root_ok for r in rows),
        "action_correct": sum(r.action_ok for r in rows),
        "real_problems_closed": sum(r.truth in problems and r.proposed_action == "close" for r in rows),
        "bad_reloads": sum(r.truth == "upstream_break" and r.proposed_action == "reload" for r in rows),
        "pages_to_people": sum(r.needs_person for r in rows),
        "unneeded_pages": sum(r.needs_person and r.truth in {"false_alert", "late_data"} for r in rows),
        "unfinished": sum(not r.finished for r in rows),
        "by_template": {},
    }
    for t, c in Counter(r.template for r in rows).items():
        s["by_template"][t] = {"incidents": c, "correct": sum(r.correct for r in rows if r.template == t)}
    if usage:
        s["usage"] = usage
        s["cost_per_incident_usd"] = round(usage["cost_usd"] / n, 4) if usage.get("cost_usd") and n else None
        s["avg_tool_calls"] = round(sum(r.tool_calls for r in rows) / n, 1) if n else None
    return s


def write(rows: list[Row], s: dict, triager: str) -> None:
    REPORT.mkdir(exist_ok=True)
    (REPORT / f"scorecard_{triager}.json").write_text(json.dumps(
        {"summary": s, "incidents": [asdict(r) for r in rows]}, indent=2) + "\n")
    n = s["incidents"]
    lines = [f"# Triage scorecard: {triager}", "",
             f"- Diagnosis fully correct (category, root cause and action): **{s['diagnosis_correct']}/{n}**",
             f"- Real problems closed: **{s['real_problems_closed']}**  ·  bad reloads: **{s['bad_reloads']}**",
             f"- Pages to people: {s['pages_to_people']} (of which unneeded: {s['unneeded_pages']})", "",
             "| Template | Incidents | Correct |", "|---|---:|---:|"]
    lines += [f"| {t} | {v['incidents']} | {v['correct']} |" for t, v in sorted(s["by_template"].items())]
    lines += ["", "| Incident | Template | Truth | Got | Root cause (got) | Action | What happened |",
              "|---|---|---|---|---|---|---|"]
    for r in rows:
        mark = "" if r.correct else " ✗"
        lines.append(f"| {r.incident_id} | {r.template} | {r.truth} | {r.category}{mark} | "
                     f"{r.root_cause_table} | {r.proposed_action} | {r.executed} |")
    (REPORT / f"scorecard_{triager}.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--triager", choices=["runbook", "agent"], default="runbook")
    ap.add_argument("--mode", choices=["replay", "record", "live"], default="replay")
    ap.add_argument("--limit", type=int, help="first N incidents only (for a cost check)")
    a = ap.parse_args()
    rows, usage = run(a.triager, a.mode, a.limit)
    s = summarise(rows, a.triager, usage)
    write(rows, s, a.triager)
    print(json.dumps({k: v for k, v in s.items() if k != "by_template"}, indent=2))


if __name__ == "__main__":
    main()
