import json
from collections import Counter

import pytest

from triage import tools as T
from triage.agent import investigate
from triage.evaluate import score, summarise
from triage.incidents import all_incidents, materialise
from triage.llm import LLM, MissingRecording
from triage.policy import decide
from triage.warehouse import evaluate_check

INCIDENTS = all_incidents()
BY_TEMPLATE = {}
for i in INCIDENTS:
    BY_TEMPLATE.setdefault(i.template, i)


# --- the labelled set is what it claims to be -------------------------------

def test_set_is_balanced_and_ids_unique():
    assert len(INCIDENTS) == 48
    assert len({i.incident_id for i in INCIDENTS}) == 48
    assert Counter(i.category for i in INCIDENTS) == {
        "real_break": 16, "upstream_break": 14, "late_data": 9, "false_alert": 9}


@pytest.mark.parametrize("template", sorted(BY_TEMPLATE))
def test_each_template_fires_its_check(template):
    inc = BY_TEMPLATE[template]
    con, alert = materialise(inc)
    _, ok_now = evaluate_check(con, inc.check_id, inc.now, inc.day)
    if inc.category == "late_data":
        assert ok_now, "late data should have landed by now"
    else:
        assert not ok_now, "the check should still fail when the agent looks"
    assert alert["incident_id"] == inc.incident_id and "category" not in alert


def test_upstream_breaks_really_are_in_the_raw_feed():
    inc = BY_TEMPLATE["vendor_timezone"]
    con, _ = materialise(inc)
    bad = con.execute("SELECT COUNT(*) FROM raw.orders WHERE order_ts > _loaded_at").fetchone()[0]
    assert bad > 0


# --- tools are read-only ---------------------------------------------------

@pytest.fixture(scope="module")
def con():
    c, _ = materialise(INCIDENTS[0])
    yield c
    c.close()


@pytest.mark.parametrize("sql", ["DELETE FROM raw.orders", "DROP TABLE raw.orders",
                                 "UPDATE raw.orders SET amount = 0", "SELECT 1; DELETE FROM raw.orders",
                                 "CREATE TABLE x AS SELECT 1"])
def test_run_sql_refuses_writes(con, sql):
    text, is_error = T.call(con, "run_sql", {"sql": sql})
    assert is_error and "only a single SELECT" in text
    assert con.execute("SELECT COUNT(*) FROM raw.orders").fetchone()[0] > 0


def test_run_sql_caps_rows(con):
    out = json.loads(T.run_sql(con, "SELECT * FROM raw.orders"))
    assert len(out["rows"]) == T.MAX_ROWS and out["truncated"]


def test_describe_table_shows_tier_owner_and_lineage(con):
    info = json.loads(T.describe_table(con, "analytics.fct_daily_revenue"))
    assert info["tier"] == 1 and info["owner"] == "finance-analytics"
    assert set(info["parents"]) == {"analytics.stg_orders", "raw.payments"}


def test_unknown_table_is_an_error_not_a_crash(con):
    text, is_error = T.call(con, "describe_table", {"table": "analytics.nope"})
    assert is_error and "unknown table" in text


def test_tool_schemas_are_strict():
    for tool in T.TOOLS:
        assert tool["strict"] and tool["input_schema"]["additionalProperties"] is False


# --- policy gate -----------------------------------------------------------

def test_tier1_reload_needs_approval_tier3_runs():
    assert decide("reload", 1, "x").executed == "approval_requested"
    assert decide("reload", 3, "x").executed == "reloaded"
    assert decide("escalate", 3, "x").needs_person
    assert not decide("close", 1, "x").needs_person


# --- scoring ---------------------------------------------------------------

TIERS = {"raw.orders": 1, "analytics.fct_email_engagement": 3}
OWNERS = {"raw.orders": "vendor-integrations"}


def test_closing_a_vendor_break_counts_as_unsafe():
    inc = BY_TEMPLATE["vendor_timezone"]
    row = score(inc, {"category": "false_alert", "root_cause_table": "raw.orders",
                      "proposed_action": "close", "evidence": "", "owner_note": ""}, TIERS, OWNERS)
    s = summarise([row], "t", None)
    assert s["real_problems_closed"] == 1 and s["diagnosis_correct"] == 0


def test_no_diagnosis_means_escalate():
    row = score(BY_TEMPLATE["quiet_sunday"], None, TIERS, OWNERS)
    assert row.proposed_action == "escalate" and row.needs_person and not row.correct


# --- agent loop with a scripted model --------------------------------------

def scripted(turns):
    it = iter(turns)

    def fake(self, system, messages, tools):
        return {"content": next(it), "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 5}}
    return fake


def test_agent_calls_tools_then_submits(monkeypatch, tmp_path):
    inc = BY_TEMPLATE["vendor_timezone"]
    con, alert = materialise(inc)
    diag = {"category": "upstream_break", "root_cause_table": "raw.orders", "proposed_action": "escalate",
            "evidence": "orders dated after load time in raw.orders", "owner_note": "vendor timezone"}
    monkeypatch.setattr(LLM, "_call", scripted([
        [{"type": "tool_use", "id": "t1", "name": "run_sql",
          "input": {"sql": "SELECT COUNT(*) AS n FROM raw.orders WHERE order_ts > _loaded_at"}}],
        [{"type": "tool_use", "id": "t2", "name": "submit_diagnosis", "input": diag}],
    ]))
    trace = investigate(LLM("record", tmp_path), con, alert)
    assert trace.finished and trace.tool_calls == 1 and trace.diagnosis == diag
    assert score(inc, trace.diagnosis, TIERS, OWNERS).correct
    # The recorded run replays without the API.
    monkeypatch.setattr(LLM, "_call", lambda *a: pytest.fail("replay must not call the API"))
    con2, _ = materialise(inc)
    assert investigate(LLM("replay", tmp_path), con2, alert).diagnosis == diag


def test_replay_without_recordings_fails_loudly(tmp_path):
    con, alert = materialise(INCIDENTS[0])
    with pytest.raises(MissingRecording):
        investigate(LLM("replay", tmp_path), con, alert)
