"""A small, synthetic Harbor & Pine warehouse that each incident is planted into.

Layers
  raw.*        vendor feeds (orders from the shop API, payments, email events)
  analytics.*  models built from raw: stg_orders, fct_daily_revenue,
               fct_email_engagement, dim_customers
  meta.*       what an on-call engineer looks at: table tiers and owners,
               lineage, pipeline runs, data-quality check definitions

Everything is seeded. `build(end_day)` returns an in-memory DuckDB holding 60
days of history ending on `end_day`, with a weekly pattern (Sundays are quiet).
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

import duckdb
import pandas as pd

TABLES = [
    # name, tier, owner, freshness SLA (h), description
    ("raw.orders", 1, "vendor-integrations", 24, "Orders from the shop API, loaded nightly"),
    ("raw.payments", 1, "vendor-integrations", 24, "Captured payments from the payment processor"),
    ("raw.email_events", 3, "vendor-integrations", 48, "Email sends, opens and clicks from the ESP"),
    ("analytics.stg_orders", 1, "data-platform", 24, "One row per order, latest load wins. customer_id is NULL for guest checkouts (about 3% of orders)."),
    ("analytics.fct_daily_revenue", 1, "finance-analytics", 24, "Captured revenue per day; feeds the CFO dashboard"),
    ("analytics.dim_customers", 2, "crm-analytics", 24, "Customer first and last order"),
    ("analytics.fct_email_engagement", 3, "growth", 48, "Email events per day and type"),
]
LINEAGE = [
    ("raw.orders", "analytics.stg_orders"),
    ("analytics.stg_orders", "analytics.fct_daily_revenue"),
    ("raw.payments", "analytics.fct_daily_revenue"),
    ("analytics.stg_orders", "analytics.dim_customers"),
    ("raw.email_events", "analytics.fct_email_engagement"),
]
# Order the nightly scheduler builds the models in, and when each normally finishes.
SCHEDULE = [
    ("raw.orders", time(1, 0), time(1, 20)),
    ("raw.payments", time(1, 0), time(1, 15)),
    ("raw.email_events", time(1, 30), time(1, 50)),
    ("analytics.stg_orders", time(2, 0), time(2, 10)),
    ("analytics.dim_customers", time(2, 20), time(2, 25)),
    ("analytics.fct_daily_revenue", time(2, 30), time(2, 40)),
    ("analytics.fct_email_engagement", time(2, 30), time(2, 35)),
]
CHECKS = [
    # id, table, type, description, sql returning one number, rule
    ("freshness_stg_orders", "analytics.stg_orders", "freshness",
     "Hours since the newest load", "SELECT date_diff('minute', MAX(_loaded_at), TIMESTAMP '{now}') / 60.0 FROM analytics.stg_orders", "<= 24"),
    ("freshness_fct_daily_revenue", "analytics.fct_daily_revenue", "freshness",
     "Hours since the newest load", "SELECT date_diff('minute', MAX(_loaded_at), TIMESTAMP '{now}') / 60.0 FROM analytics.fct_daily_revenue", "<= 24"),
    ("freshness_dim_customers", "analytics.dim_customers", "freshness",
     "Hours since the newest load", "SELECT date_diff('minute', MAX(_loaded_at), TIMESTAMP '{now}') / 60.0 FROM analytics.dim_customers", "<= 24"),
    ("freshness_fct_email_engagement", "analytics.fct_email_engagement", "freshness",
     "Hours since the newest load", "SELECT date_diff('minute', MAX(_loaded_at), TIMESTAMP '{now}') / 60.0 FROM analytics.fct_email_engagement", "<= 48"),
    ("volume_stg_orders", "analytics.stg_orders", "volume",
     "Orders on the latest day vs the trailing 7-day average",
     "SELECT COUNT(*) FILTER (WHERE order_ts::DATE = DATE '{day}') / (COUNT(*) FILTER (WHERE order_ts::DATE BETWEEN DATE '{day}' - 7 AND DATE '{day}' - 1) / 7.0) FROM analytics.stg_orders", ">= 0.6"),
    ("volume_fct_daily_revenue", "analytics.fct_daily_revenue", "volume",
     "Revenue on the latest day vs the trailing 7-day average",
     "SELECT MAX(revenue) FILTER (WHERE day = DATE '{day}') / AVG(revenue) FILTER (WHERE day BETWEEN DATE '{day}' - 7 AND DATE '{day}' - 1) FROM analytics.fct_daily_revenue", ">= 0.6"),
    ("volume_fct_email_engagement", "analytics.fct_email_engagement", "volume",
     "Email events on the latest day vs the trailing 7-day average",
     "SELECT SUM(events) FILTER (WHERE day = DATE '{day}') / (SUM(events) FILTER (WHERE day BETWEEN DATE '{day}' - 7 AND DATE '{day}' - 1) / 7.0) FROM analytics.fct_email_engagement", ">= 0.6"),
    ("not_null_stg_orders_customer_id", "analytics.stg_orders", "not_null",
     "Orders with no customer_id", "SELECT COUNT(*) FROM analytics.stg_orders WHERE customer_id IS NULL", "= 0"),
    ("range_stg_orders_order_ts", "analytics.stg_orders", "accepted_range",
     "Orders dated after the load time", "SELECT COUNT(*) FROM analytics.stg_orders WHERE order_ts > _loaded_at", "= 0"),
    ("not_null_raw_payments_order_id", "raw.payments", "not_null",
     "Payments with no order_id", "SELECT COUNT(*) FROM raw.payments WHERE order_id IS NULL", "= 0"),
]


def _daily_volume(rng: random.Random, d: date, base: int) -> int:
    factor = 0.42 if d.weekday() == 6 else 1.0  # Sundays are quiet
    return max(1, int(base * factor * rng.uniform(0.85, 1.15)))


def build(end_day: date, seed: int = 7, days: int = 60) -> duckdb.DuckDBPyConnection:
    rng = random.Random(seed)
    start = end_day - timedelta(days=days - 1)
    orders, payments, emails = [], [], []
    oid = pid = eid = 0
    for i in range(days):
        d = start + timedelta(days=i)
        loaded = datetime.combine(d + timedelta(days=1), time(1, 20))
        for _ in range(_daily_volume(rng, d, 80)):
            oid += 1
            ts = datetime.combine(d, time(0)) + timedelta(minutes=rng.randint(0, 1439))
            cust = None if rng.random() < 0.03 else rng.randint(1, 2500)  # guest checkout
            amount = round(rng.uniform(20, 400), 2)
            orders.append((oid, cust, ts, amount, "placed", loaded))
            pid += 1
            payments.append((pid, oid, amount, ts + timedelta(minutes=rng.randint(1, 30)),
                             datetime.combine(d + timedelta(days=1), time(1, 15))))
        for _ in range(_daily_volume(rng, d, 600)):
            eid += 1
            emails.append((eid, rng.randint(1, 2500), rng.choice(["send", "send", "open", "click"]),
                           datetime.combine(d, time(0)) + timedelta(minutes=rng.randint(0, 1439)),
                           datetime.combine(d + timedelta(days=1), time(1, 50))))

    con = duckdb.connect(":memory:")
    for s in ("raw", "analytics", "meta"):
        con.execute(f"CREATE SCHEMA {s}")
    frames = {
        "raw.orders": pd.DataFrame(orders, columns=["order_id", "customer_id", "order_ts", "amount", "status", "_loaded_at"]),
        "raw.payments": pd.DataFrame(payments, columns=["payment_id", "order_id", "amount", "paid_ts", "_loaded_at"]),
        "raw.email_events": pd.DataFrame(emails, columns=["event_id", "customer_id", "event_type", "event_ts", "_loaded_at"]),
    }
    for name, df in frames.items():
        con.register("df", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM df")
        con.unregister("df")
    for name in ("analytics.stg_orders", "analytics.dim_customers",
                 "analytics.fct_daily_revenue", "analytics.fct_email_engagement"):
        build_model(con, name)

    con.execute("CREATE TABLE meta.tables (table_name VARCHAR, tier INTEGER, owner VARCHAR, freshness_sla_hours INTEGER, description VARCHAR)")
    con.executemany("INSERT INTO meta.tables VALUES (?, ?, ?, ?, ?)", TABLES)
    con.execute("CREATE TABLE meta.lineage (parent VARCHAR, child VARCHAR)")
    con.executemany("INSERT INTO meta.lineage VALUES (?, ?)", LINEAGE)
    con.execute("CREATE TABLE meta.dq_checks (check_id VARCHAR, table_name VARCHAR, check_type VARCHAR, description VARCHAR, sql VARCHAR, rule VARCHAR)")
    con.executemany("INSERT INTO meta.dq_checks VALUES (?, ?, ?, ?, ?, ?)", CHECKS)

    runs = []
    rid = 0
    for i in range(days - 14, days):  # two weeks of normal runs
        run_day = start + timedelta(days=i + 1)
        for table, t0, t1 in SCHEDULE:
            rid += 1
            jitter = timedelta(minutes=rng.randint(0, 6))
            runs.append((rid, table, datetime.combine(run_day, t0) + jitter,
                         datetime.combine(run_day, t1) + jitter, "success", None))
    con.execute("CREATE TABLE meta.pipeline_runs (run_id INTEGER, table_name VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR, error VARCHAR)")
    con.executemany("INSERT INTO meta.pipeline_runs VALUES (?, ?, ?, ?, ?, ?)", runs)
    return con


def build_model(con: duckdb.DuckDBPyConnection, name: str, loaded_at: str | None = None) -> None:
    """(Re)build one analytics model from its parents, as the nightly job would."""
    stamp = f"TIMESTAMP '{loaded_at}'" if loaded_at else None
    sql = {
        "analytics.stg_orders": f"""
            SELECT order_id, customer_id, order_ts, amount, status,
                   {stamp or '_loaded_at'} AS _loaded_at
            FROM raw.orders
            QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY _loaded_at DESC) = 1""",
        "analytics.dim_customers": f"""
            SELECT customer_id, MIN(order_ts)::DATE AS first_order, MAX(order_ts)::DATE AS last_order,
                   COUNT(*) AS orders, {stamp or 'MAX(_loaded_at)'} AS _loaded_at
            FROM analytics.stg_orders WHERE customer_id IS NOT NULL GROUP BY customer_id""",
        "analytics.fct_daily_revenue": f"""
            SELECT o.order_ts::DATE AS day, SUM(p.amount) AS revenue, COUNT(*) AS orders,
                   {stamp or 'MAX(o._loaded_at)'} AS _loaded_at
            FROM analytics.stg_orders o JOIN raw.payments p ON p.order_id = o.order_id
            GROUP BY 1""",
        "analytics.fct_email_engagement": f"""
            SELECT event_ts::DATE AS day, event_type, COUNT(*) AS events,
                   {stamp or 'MAX(_loaded_at)'} AS _loaded_at
            FROM raw.email_events GROUP BY 1, 2""",
    }[name]
    con.execute(f"CREATE OR REPLACE TABLE {name} AS {sql}")


def evaluate_check(con: duckdb.DuckDBPyConnection, check_id: str, now: datetime, day: date) -> tuple[float | None, bool]:
    sql, rule = con.execute("SELECT sql, rule FROM meta.dq_checks WHERE check_id = ?", [check_id]).fetchone()
    value = con.execute(sql.format(now=now.strftime("%Y-%m-%d %H:%M:%S"), day=day.isoformat())).fetchone()[0]
    op, limit = rule.split()
    limit = float(limit)
    if value is None:
        return None, False
    ok = {"<=": value <= limit, ">=": value >= limit, "=": value == limit}[op]
    return float(value), ok
