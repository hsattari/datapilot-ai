"""Structured plan -> controlled SQL template -> validation -> read-only execution.

The model (if any) only proposes a plan. Permissions and SQL shape are enforced here, in code.
"""
import threading
from datetime import date
from typing import Literal

import sqlglot
from pydantic import BaseModel, Field, field_validator
from sqlglot import exp

from ..config import get_settings
from . import analytics_store as store

DIM_SQL = {
    "month": "strftime(order_date, '%Y-%m')",
    "product": "product",
    "region": "region",
    "customer": "customer",
}
AGG_SQL = {"sum": "SUM({e})", "count_distinct": "COUNT(DISTINCT {e})", "avg": "AVG({e})"}
ALLOWED_TABLES = {"fact_sales"}
ALLOWED_FUNCS = {"SUM", "COUNT", "AVG", "STRFTIME"}


class QueryRejected(ValueError):
    pass


class TimeRange(BaseModel):
    type: Literal["all_time", "last_n_complete_months"] = "all_time"
    n: int = Field(default=12, ge=1, le=60)


class Filter(BaseModel):
    field: Literal["product", "region", "customer"]
    value: str = Field(max_length=200)


class Plan(BaseModel):
    intent: Literal["metric_total", "compare_metric_over_time", "metric_breakdown"]
    metric: str
    dimensions: list[Literal["month", "product", "region", "customer"]] = Field(default_factory=list, max_length=2)
    time_range: TimeRange = TimeRange()
    filters: list[Filter] = Field(default_factory=list, max_length=5)
    visualization: Literal["number", "line_chart", "bar_chart", "table"] = "table"
    limit: int = Field(default=100, ge=1, le=1000)

    @field_validator("metric")
    @classmethod
    def _metric_name(cls, v: str) -> str:
        if not v.replace("_", "").isalnum():
            raise ValueError("bad metric name")
        return v


def compile_plan(plan: Plan, expression: str, aggregation: str, today: date | None = None):
    """Build SQL from a template; user-controlled values are bound parameters only."""
    today = today or date.today()
    agg = AGG_SQL[aggregation].format(e=expression)  # expression was validated at registration
    dims = [f"{DIM_SQL[d]} AS {d}" for d in plan.dimensions]
    where, params = [], []
    if plan.time_range.type == "last_n_complete_months":
        first_this = today.replace(day=1)
        y, m = first_this.year, first_this.month - plan.time_range.n
        while m <= 0:
            m += 12
            y -= 1
        where += ["order_date >= ?", "order_date < ?"]
        params += [date(y, m, 1).isoformat(), first_this.isoformat()]
    for f in plan.filters:
        where.append(f"{f.field} = ?")
        params.append(f.value)
    sql = f"SELECT {', '.join(dims + [agg + ' AS value'])} FROM fact_sales"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if plan.dimensions:
        sql += " GROUP BY " + ", ".join(str(i + 1) for i in range(len(plan.dimensions)))
        sql += " ORDER BY " + (", ".join(["1"]) if "month" in plan.dimensions else "value DESC")
    sql += f" LIMIT {min(plan.limit, get_settings().query_row_limit)}"
    return sql, params


def validate_select(sql: str) -> None:
    """Defence in depth: a single read-only SELECT over allowlisted tables and functions."""
    try:
        stmts = sqlglot.parse(sql, dialect="duckdb")
    except sqlglot.errors.ParseError as e:
        raise QueryRejected(f"Unparseable SQL: {e}")
    stmts = [s for s in stmts if s is not None]
    if len(stmts) != 1:
        raise QueryRejected("Exactly one statement is allowed")
    tree = stmts[0]
    if not isinstance(tree, exp.Select):
        raise QueryRejected("Only SELECT statements are allowed")
    for bad in (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Command,
                exp.Alter, exp.Subquery, exp.Union, exp.Into, exp.Copy):
        if tree.find(bad):
            raise QueryRejected(f"Disallowed construct: {bad.__name__}")
    for t in tree.find_all(exp.Table):
        if t.name.lower() not in ALLOWED_TABLES or t.args.get("db"):
            raise QueryRejected(f"Table not allowed: {t.name}")
    allowed_nodes = (exp.Sum, exp.Count, exp.Avg, exp.Distinct, exp.TimeToStr, exp.Cast,
                     exp.TryCast, exp.Paren, exp.Binary, exp.Unary)
    for fn in tree.find_all(exp.Func):
        if isinstance(fn, (exp.Column, exp.Literal)):
            continue
        if not isinstance(fn, allowed_nodes) and fn.sql_name().upper() not in ALLOWED_FUNCS:
            raise QueryRejected(f"Function not allowed: {fn.sql_name()}")
    if not tree.args.get("limit"):
        raise QueryRejected("A LIMIT is required")


def execute_readonly(tenant_id: str, sql: str, params: list) -> tuple[list[str], list[list]]:
    validate_select(sql)
    s = get_settings()
    con = store.reader(tenant_id)  # read-only connection scoped to this tenant's database file
    timer = threading.Timer(s.query_timeout_seconds, con.interrupt)
    timer.start()
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchmany(s.query_row_limit)]
        return cols, rows
    finally:
        timer.cancel()
        con.close()
