"""The tools the triage agent may call. All are read-only.

The agent can look but not touch: run_sql accepts a single SELECT (or DESCRIBE),
and results are capped. Acting (close, reload, escalate) is not a tool; the agent
only proposes, and policy.py decides what actually happens.
"""
from __future__ import annotations

import json

import duckdb
import sqlglot
from sqlglot import exp

MAX_ROWS = 40


def _strict(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": properties, "required": required,
                             "additionalProperties": False}}


TOOLS = [
    _strict("run_sql", "Run one read-only SELECT against the warehouse (DuckDB SQL). "
            f"Schemas: raw, analytics, meta. At most {MAX_ROWS} rows come back.",
            {"sql": {"type": "string"}}, ["sql"]),
    _strict("describe_table", "Tier, owner, freshness SLA, description, columns, row count, "
            "parents and children of a table (e.g. analytics.stg_orders).",
            {"table": {"type": "string"}}, ["table"]),
    _strict("pipeline_runs", "Recent nightly runs of a table: start, finish, status, error.",
            {"table": {"type": "string"}, "limit": {"type": "integer"}}, ["table", "limit"]),
    _strict("get_check", "Definition of a data-quality check: its SQL and pass rule.",
            {"check_id": {"type": "string"}}, ["check_id"]),
    _strict("submit_diagnosis", "Finish the investigation. Call exactly once.", {
        "category": {"type": "string", "enum": ["false_alert", "late_data", "real_break", "upstream_break"]},
        "root_cause_table": {"type": "string"},
        "proposed_action": {"type": "string", "enum": ["close", "reload", "escalate"]},
        "evidence": {"type": "string", "description": "The facts, with numbers, that support the diagnosis."},
        "owner_note": {"type": "string", "description": "Two or three sentences for the table owner."},
    }, ["category", "root_cause_table", "proposed_action", "evidence", "owner_note"]),
]


def _table_ref(con: duckdb.DuckDBPyConnection, table: str) -> str:
    known = {r[0] for r in con.execute(
        "SELECT table_schema || '.' || table_name FROM information_schema.tables").fetchall()}
    if table not in known:
        raise ValueError(f"unknown table {table!r}; known: {', '.join(sorted(known))}")
    return table


def run_sql(con, sql: str) -> str:
    parsed = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    if len(parsed) != 1 or not isinstance(parsed[0], (exp.Select, exp.Union, exp.Describe)):
        raise ValueError("only a single SELECT or DESCRIBE is allowed")
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchmany(MAX_ROWS + 1)
    more = len(rows) > MAX_ROWS
    body = [dict(zip(cols, r)) for r in rows[:MAX_ROWS]]
    return json.dumps({"rows": body, "truncated": more}, default=str)


def describe_table(con, table: str) -> str:
    t = _table_ref(con, table)
    meta = con.execute("SELECT tier, owner, freshness_sla_hours, description FROM meta.tables WHERE table_name = ?",
                       [t]).fetchone()
    cols = [f"{n} {ty}" for n, ty, *_ in con.execute(f"DESCRIBE {t}").fetchall()]
    rows = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    parents = [r[0] for r in con.execute("SELECT parent FROM meta.lineage WHERE child = ?", [t]).fetchall()]
    children = [r[0] for r in con.execute("SELECT child FROM meta.lineage WHERE parent = ?", [t]).fetchall()]
    info = {"table": t, "columns": cols, "row_count": rows, "parents": parents, "children": children}
    if meta:
        info.update(dict(zip(["tier", "owner", "freshness_sla_hours", "description"], meta)))
    return json.dumps(info, default=str)


def pipeline_runs(con, table: str, limit: int) -> str:
    t = _table_ref(con, table)
    rows = con.execute("SELECT run_id, started_at, finished_at, status, error FROM meta.pipeline_runs "
                       "WHERE table_name = ? ORDER BY started_at DESC LIMIT ?", [t, max(1, min(limit, 14))]).fetchall()
    keys = ["run_id", "started_at", "finished_at", "status", "error"]
    return json.dumps([dict(zip(keys, r)) for r in rows], default=str)


def get_check(con, check_id: str) -> str:
    row = con.execute("SELECT check_id, table_name, check_type, description, sql, rule FROM meta.dq_checks "
                      "WHERE check_id = ?", [check_id]).fetchone()
    if not row:
        raise ValueError(f"unknown check {check_id!r}")
    return json.dumps(dict(zip(["check_id", "table", "type", "description", "sql", "rule"], row)))


HANDLERS = {"run_sql": run_sql, "describe_table": describe_table,
            "pipeline_runs": pipeline_runs, "get_check": get_check}


def call(con, name: str, args: dict) -> tuple[str, bool]:
    """Returns (result text, is_error). Errors go back to the agent rather than crashing."""
    try:
        return HANDLERS[name](con, **args), False
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:400]}", True
