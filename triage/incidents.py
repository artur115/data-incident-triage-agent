"""48 labelled data incidents, each planted into a fresh copy of the warehouse.

Every incident starts as a failed data-quality check, which is all the on-call
engineer (or the agent) is given. The label says what really happened:

  category          false_alert | late_data | real_break | upstream_break
  root_cause_table  where the problem is
  action            close | reload | escalate

  false_alert     the data is fine; the check is wrong for this case (quiet Sundays,
                  a column that is allowed to be NULL)                -> close
  late_data       the load ran late; by now it has landed             -> close
  real_break      our own model is wrong (failed run, ran before its
                  parent finished); the source is fine                -> reload
  upstream_break  the vendor feed itself is wrong; reloading would
                  copy the bad data again                             -> escalate
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable

import duckdb

from .warehouse import build, build_model, evaluate_check


@dataclass(frozen=True)
class Incident:
    incident_id: str
    template: str
    day: date                       # latest business day in the data
    check_id: str
    table: str
    category: str
    root_cause_table: str
    action: str
    plant: Callable[[duckdb.DuckDBPyConnection, date], None]
    observed: float | None = None   # set when the check value at fire time is not recomputable
    fired: time = time(6, 30)
    seed: int = 7

    @property
    def now(self) -> datetime:
        return datetime.combine(self.day + timedelta(days=1), time(7, 0))

    @property
    def fired_at(self) -> datetime:
        return datetime.combine(self.day + timedelta(days=1), self.fired)


def _last_run(con, table: str, day: date):
    run_day = day + timedelta(days=1)
    return con.execute(
        "SELECT run_id FROM meta.pipeline_runs WHERE table_name = ? AND started_at::DATE = ?",
        [table, run_day]).fetchone()[0]


def _set_run(con, table, day, started: time, finished: time | None, status="success", error=None):
    run_day = day + timedelta(days=1)
    con.execute("UPDATE meta.pipeline_runs SET started_at = ?, finished_at = ?, status = ?, error = ? WHERE run_id = ?",
                [datetime.combine(run_day, started), datetime.combine(run_day, finished) if finished else None,
                 status, error, _last_run(con, table, day)])


# --- plants ------------------------------------------------------------------

def late(table):
    def plant(con, day):
        _set_run(con, table, day, time(6, 5), time(6, 52))
    return plant


def nothing(con, day):
    pass


ERRORS = ["Connection reset by peer while writing batch 3 of 5",
          "Warehouse statement timeout after 1800s",
          "Out of memory: hash join spilled past temp limit",
          "Lost connection to warehouse during commit"]


def failed_run(table, error):
    def plant(con, day):
        if table == "analytics.stg_orders":
            con.execute("DELETE FROM analytics.stg_orders WHERE order_ts::DATE = ? AND order_id % 3 <> 0", [day])
        elif table == "analytics.fct_daily_revenue":
            con.execute("DELETE FROM analytics.fct_daily_revenue WHERE day = ?", [day])
        elif table == "analytics.fct_email_engagement":
            con.execute("DELETE FROM analytics.fct_email_engagement WHERE day = ? AND event_type <> 'send'", [day])
        _set_run(con, table, day, time(2, 0), time(2, 4), status="failed", error=error)
    return plant


def ran_before_parent(con, day):
    # stg_orders ran late; fct_daily_revenue ran on schedule and read yesterday's stg.
    _set_run(con, "analytics.stg_orders", day, time(2, 35), time(3, 5))
    _set_run(con, "analytics.fct_daily_revenue", day, time(2, 30), time(2, 40))
    con.execute("DELETE FROM analytics.fct_daily_revenue WHERE day = ?", [day])


def future_timestamps(share):
    def plant(con, day):
        # The vendor started sending local time as UTC for part of the feed.
        con.execute("UPDATE raw.orders SET order_ts = order_ts + INTERVAL 8 HOUR "
                    "WHERE order_ts::DATE = ? AND order_ts::TIME >= TIME '17:30' AND order_id % ? = 0",
                    [day, share])
        build_model(con, "analytics.stg_orders")
    return plant


def null_payment_keys(con, day):
    con.execute("UPDATE raw.payments SET order_id = NULL WHERE paid_ts::DATE = ? AND payment_id % 5 < 3", [day])
    build_model(con, "analytics.fct_daily_revenue")


# --- the set ---------------------------------------------------------------

WEEKDAYS = [date(2026, 11, 19), date(2026, 11, 18), date(2026, 11, 17), date(2026, 11, 13)]
SUNDAYS = [date(2026, 11, 15), date(2026, 11, 8)]


def all_incidents() -> list[Incident]:
    out: list[Incident] = []

    def add(template, day, check, table, category, root, action, plant, **kw):
        n = len(out) + 1
        out.append(Incident(f"INC-{n:03d}", template, day, check, table, category, root, action,
                            plant, seed=100 + n, **kw))

    for day in WEEKDAYS[:3]:
        for t in ("analytics.stg_orders", "analytics.fct_daily_revenue", "analytics.dim_customers"):
            add("late_load", day, f"freshness_{t.split('.')[1]}", t, "late_data", t, "close", late(t),
                observed=29.2)
    for day in SUNDAYS:
        for check, t in (("volume_stg_orders", "analytics.stg_orders"),
                         ("volume_fct_daily_revenue", "analytics.fct_daily_revenue"),
                         ("volume_fct_email_engagement", "analytics.fct_email_engagement")):
            add("quiet_sunday", day, check, t, "false_alert", t, "close", nothing)
    for day in WEEKDAYS[:3]:
        add("nullable_column", day, "not_null_stg_orders_customer_id", "analytics.stg_orders",
            "false_alert", "analytics.stg_orders", "close", nothing)
    for i, day in enumerate(WEEKDAYS):
        for check, t in (("volume_stg_orders", "analytics.stg_orders"),
                         ("volume_fct_daily_revenue", "analytics.fct_daily_revenue"),
                         ("volume_fct_email_engagement", "analytics.fct_email_engagement")):
            add("failed_run", day, check, t, "real_break", t, "reload", failed_run(t, ERRORS[i]))
    for day in WEEKDAYS:
        add("ran_before_parent", day, "volume_fct_daily_revenue", "analytics.fct_daily_revenue",
            "real_break", "analytics.fct_daily_revenue", "reload", ran_before_parent)
    for i, day in enumerate(WEEKDAYS + WEEKDAYS[:3]):
        add("vendor_timezone", day, "range_stg_orders_order_ts", "analytics.stg_orders",
            "upstream_break", "raw.orders", "escalate", future_timestamps(2 + i % 3))
    for i, day in enumerate(WEEKDAYS + WEEKDAYS[:3]):
        check, t = (("volume_fct_daily_revenue", "analytics.fct_daily_revenue") if i % 2 == 0
                    else ("not_null_raw_payments_order_id", "raw.payments"))
        add("vendor_null_keys", day, check, t, "upstream_break", "raw.payments", "escalate", null_payment_keys)
    return out


def materialise(inc: Incident) -> tuple[duckdb.DuckDBPyConnection, dict]:
    """Build the warehouse as it looks when the agent is paged. Returns (connection, alert)."""
    con = build(inc.day, seed=inc.seed)
    inc.plant(con, inc.day)
    value = inc.observed
    if value is None:
        value, _ = evaluate_check(con, inc.check_id, inc.fired_at, inc.day)
    rule = con.execute("SELECT rule FROM meta.dq_checks WHERE check_id = ?", [inc.check_id]).fetchone()[0]
    alert = {"incident_id": inc.incident_id, "check_id": inc.check_id, "table": inc.table,
             "fired_at": inc.fired_at.isoformat(sep=" "), "observed": None if value is None else round(value, 3),
             "rule": f"value {rule}", "business_day": inc.day.isoformat(), "now": inc.now.isoformat(sep=" ")}
    return con, alert
