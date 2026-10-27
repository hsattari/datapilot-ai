"""Mock-based tests: they verify SQL generation, parameter binding, tenant schema pinning and validation.
They do NOT prove the adapter works against a real Databricks workspace."""
from datetime import date

import pandas as pd
import pytest
import sqlglot

from app.config import get_settings
from app.services import backends
from app.services.backends import DatabricksBackend
from app.services.query_engine import Plan, QueryRejected, compile_plan, validate_select

TENANT = "123e4567-e89b-12d3-a456-426614174000"


class FakeCursor:
    def __init__(self, log, results):
        self.log, self.results, self.description = log, results, [("value",)]

    def execute(self, sql, params=None):
        self.log.append((sql, params))

    def fetchone(self):
        if not self.results:
            return (0,)
        r = self.results.pop(0)
        return tuple(r) if isinstance(r, (tuple, list)) else (r,)

    def fetchmany(self, n):
        return [(1.0,)]

    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeConn:
    def __init__(self, log, results, kw):
        self.log, self.results, self.kw = log, results, kw

    def cursor(self): return FakeCursor(self.log, self.results)
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture()
def dbx(monkeypatch):
    s = get_settings()
    for k, v in dict(databricks_server_hostname="h", databricks_http_path="/p", databricks_token="WRITE",
                     databricks_readonly_token="READ", databricks_catalog="datapilot").items():
        monkeypatch.setattr(s, k, v)
    log, conns, results = [], [], []

    def fake_connect(**kw):
        conns.append(kw)
        return FakeConn(log, results, kw)

    import databricks.sql as dsql
    monkeypatch.setattr(dsql, "connect", fake_connect)
    b = DatabricksBackend()
    return b, log, conns, results


def test_requires_configuration(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "databricks_catalog", "")
    with pytest.raises(RuntimeError):
        DatabricksBackend()


def test_schema_per_tenant_and_validation(dbx):
    b, *_ = dbx
    assert b.schema(TENANT) == "t_123e4567e89b12d3a456426614174000"
    for bad in ("x; DROP", "../x", ""):
        with pytest.raises(ValueError):
            b.schema(bad)


def test_positional_to_named_params():
    sql, p = DatabricksBackend.to_named("SELECT 1 FROM fact_sales WHERE a >= ? AND b = ? LIMIT 5", ["2026-01-01", "x' OR '1'='1"])
    assert ":p0" in sql and ":p1" in sql and "?" not in sql
    assert p == {"p0": "2026-01-01", "p1": "x' OR '1'='1"}  # values are bound, never interpolated
    with pytest.raises(ValueError):
        DatabricksBackend.to_named("SELECT ? LIMIT 1", [])


def test_query_uses_readonly_token_pinned_schema_and_timeout(dbx):
    b, log, conns, _ = dbx
    cols, rows = b.query(TENANT, "SELECT SUM(net_sales) AS value FROM fact_sales WHERE region = ? LIMIT 10", ["N"])
    kw = conns[-1]
    assert kw["access_token"] == "READ" and kw["catalog"] == "datapilot" and kw["schema"] == b.schema(TENANT)
    assert kw["session_configuration"]["STATEMENT_TIMEOUT"] == "15"
    assert log[-1][1] == {"p0": "N"} and rows == [[1.0]]


def test_load_bronze_and_build_fact_sales_sql(dbx):
    b, log, conns, results = dbx
    df = pd.DataFrame({"order_id": ["A", "B"], "order_date": ["2026-01-10", "2026-01-11"], "net_sales": ["1", "2"],
                       "cost": ["1", "1"]})
    b.load_bronze(TENANT, "bronze_x", df)
    assert conns[-1]["access_token"] == "WRITE"
    stmts = [s for s, _ in log]
    assert "schema" not in conns[-1]  # writers must not pin a schema that may not exist yet
    assert stmts[0].startswith("CREATE SCHEMA IF NOT EXISTS t_") and stmts[1].startswith("USE SCHEMA t_")
    assert "CREATE OR REPLACE TABLE bronze_x" in stmts[2]
    assert all("INSERT INTO bronze_x VALUES" in s for s in stmts[3:])
    assert all(isinstance(p, dict) and ":p0_0" in s and "'A'" not in s for s, p in log[3:])  # values only in bound params
    with pytest.raises(ValueError):
        b.load_bronze(TENANT, "bad;name", df)

    log.clear()
    # reads in order: total staged, (replaced rows, replaced orders), loaded, rejected, fact total, files, cost missing
    results += [2, (1, 1), 2, 0, 5, 2, 0]
    out = b.build_fact_sales(TENANT, "bronze_x", {"order_id": "order_id", "order_date": "order_date",
                                                  "net_sales": "net_sales", "cost_of_goods_sold": "cost"},
                             "11111111-2222-3333-4444-555555555555")
    assert out == {"rows_loaded": 2, "rows_rejected": 0, "cost_missing_rows": 0, "has_cost": True,
                   "rows_replaced": 1, "orders_replaced": 1, "fact_rows_total": 5, "files_in_model": 2,
                   "replace_all": False}
    for s, _ in log:  # every generated statement must parse as Databricks SQL
        sqlglot.parse_one(s, dialect="databricks")
    stmts = [s for s, _ in log]
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS silver_sales") for s in stmts)   # files accumulate
    assert not any("CREATE OR REPLACE TABLE silver_sales" in s for s in stmts)
    assert any(s.startswith("DELETE FROM silver_sales WHERE order_id IN") for s in stmts)  # upsert by order id
    assert any(s.startswith("CREATE OR REPLACE TABLE fact_sales AS SELECT") and "FROM silver_sales" in s for s in stmts)
    assert not any("dataset_id <>" in s for s in stmts)                                      # no replace_all by default
    log.clear(); results[:] = [2, (0, 0), 2, 0, 2, 1, 0]
    b.build_fact_sales(TENANT, "bronze_x", {"order_id": "order_id", "order_date": "order_date", "net_sales": "net_sales"},
                       "11111111-2222-3333-4444-555555555555", replace_all=True)
    assert any("DELETE FROM silver_sales WHERE dataset_id <>" in s for s, _ in log)          # explicit replace
    with pytest.raises(ValueError):                                                          # dataset id is validated
        b.build_fact_sales(TENANT, "bronze_x", {"order_id": "order_id", "order_date": "order_date",
                                                "net_sales": "net_sales"}, "x'; DROP TABLE y --")
    results[:] = [2, (0, 0), 2, 1, 4, 1, 0]  # reconciliation failure must raise, not pass silently
    with pytest.raises(AssertionError):
        b.build_fact_sales(TENANT, "bronze_x", {"order_id": "order_id", "order_date": "order_date",
                                                "net_sales": "net_sales"}, "11111111-2222-3333-4444-555555555555")


def test_compiled_plan_is_valid_databricks_sql(dbx):
    b, *_ = dbx
    plan = Plan(intent="compare_metric_over_time", metric="net_revenue", dimensions=["month", "region"],
                time_range={"type": "last_n_complete_months", "n": 3}, filters=[{"field": "product", "value": "W"}])
    sql, params = compile_plan(plan, "net_sales - cost_of_goods_sold", "sum", date(2026, 4, 15), b.month_expr("order_date"))
    assert "date_format(order_date, 'yyyy-MM')" in sql
    validate_select(sql, b.dialect)
    for bad in ("SELECT * FROM catalog.schema.fact_sales LIMIT 1", "SELECT * FROM other LIMIT 1",
                "DROP TABLE fact_sales", "SELECT * FROM fact_sales"):
        with pytest.raises(QueryRejected):
            validate_select(bad, b.dialect)


def test_backend_selection(monkeypatch):
    backends._make.cache_clear()
    assert backends.get_backend().name == "duckdb"
    backends._make.cache_clear()
