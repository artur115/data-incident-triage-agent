"""The triage agent: a tool-use loop on the Messages API.

It gets one failed check, investigates with read-only tools, and ends by calling
submit_diagnosis. If it runs out of turns or never submits, the incident is
escalated: when unsure, a person looks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import tools as T
from .llm import LLM

SYSTEM = """You are the on-call data reliability agent for Harbor & Pine's warehouse.
A data-quality check has failed. Find out what really happened, then call submit_diagnosis.

Categories
- false_alert: the data is correct; the check is wrong for this case (for example a normal
  weekly pattern, or a column that is allowed to be NULL). Action: close.
- late_data: a load ran late; the data has landed by now and the check would pass if rerun. Action: close.
- real_break: one of our analytics models is wrong (failed run, or it ran before its parent
  finished) while its sources are correct. Rebuilding it fixes it. Action: reload.
- upstream_break: a raw vendor feed is wrong. Reloading would copy the bad data again.
  Action: escalate to the owner of the raw table.

How to work
- Look before you conclude: rerun the check, read the pipeline runs, compare with history,
  and follow lineage to the parents.
- root_cause_table is where the problem is, which may be upstream of the alerted table.
- Never close a real problem. If the evidence is mixed, escalate.
- Evidence must cite numbers you saw in tool results.
- "now" in the alert is the current time; data timestamps are in the same clock.
"""

MAX_TURNS = 14


@dataclass
class Trace:
    diagnosis: dict | None = None
    turns: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    finished: bool = False
    note: str = ""
    calls: list[str] = field(default_factory=list)


def investigate(llm: LLM, con, alert: dict) -> Trace:
    trace = Trace()
    messages: list[dict] = [{"role": "user", "content":
                             "A data-quality check failed:\n```json\n" + json.dumps(alert, indent=2) + "\n```"}]
    reminded = False
    for _ in range(MAX_TURNS):
        trace.turns += 1
        resp = llm.create(SYSTEM, messages, T.TOOLS, tag=alert["incident_id"])
        if resp["stop_reason"] == "refusal":
            trace.note = "model declined"
            return trace
        messages.append({"role": "assistant", "content": resp["content"]})
        uses = [b for b in resp["content"] if b.get("type") == "tool_use"]
        if not uses:
            if reminded:
                trace.note = "ended without a diagnosis"
                return trace
            reminded = True
            messages.append({"role": "user", "content": "Call submit_diagnosis to finish."})
            continue
        results = []
        for u in uses:
            trace.calls.append(u["name"])
            if u["name"] == "submit_diagnosis":
                trace.diagnosis, trace.finished = u["input"], True
                return trace
            trace.tool_calls += 1
            text, is_error = T.call(con, u["name"], u["input"])
            trace.tool_errors += is_error
            results.append({"type": "tool_result", "tool_use_id": u["id"], "content": text,
                            **({"is_error": True} if is_error else {})})
        messages.append({"role": "user", "content": results})
    trace.note = f"no diagnosis after {MAX_TURNS} turns"
    return trace
