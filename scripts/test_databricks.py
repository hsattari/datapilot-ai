"""End-to-end check of the Databricks backend against YOUR workspace. Run from the repo root:

    pip install -r apps/api/requirements.txt
    python scripts/test_databricks.py

Needs in .env: DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH, DATABRICKS_CATALOG, DATABRICKS_TOKEN
(optional DATABRICKS_READONLY_TOKEN to also test the SELECT-only principal).
It creates a throwaway tenant schema (t_<random>) in your catalog, loads the 3-row reference dataset,
checks gross profit = 140, then DROPS the schema. The first run can take a few minutes while the
SQL warehouse starts. Secrets are never printed.
"""
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

import pandas as pd  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.backends import DatabricksBackend  # noqa: E402
from app.services.query_engine import Plan, compile_plan, validate_select  # noqa: E402

s = get_settings()
missing = [k.upper() for k in ("databricks_server_hostname", "databricks_http_path", "databricks_catalog",
                               "databricks_token") if not getattr(s, k)]
if missing:
    sys.exit(f"Missing in .env: {', '.join(missing)}")
if s.databricks_server_hostname.startswith("http"):
    sys.exit("DATABRICKS_SERVER_HOSTNAME must be the host only (no https://)")


def step(name, fn):
    try:
        out = fn()
        print(f"  OK    {name}" + (f": {out}" if out is not None else ""))
        return True, out
    except Exception as e:  # report, never print credentials
        print(f"  FAIL  {name}: {type(e).__name__}: {str(e)[:400]}")
        return False, None


b = DatabricksBackend()
tenant = str(uuid.uuid4())
schema = b.schema(tenant)
print(f"Host: {s.databricks_server_hostname}  Catalog: {s.databricks_catalog}  Temp schema: {schema}")
df = pd.read_csv(ROOT / "data" / "synthetic" / "orders_reference.csv", dtype=str)
mapping = {"order_id": "order_id", "order_date": "order_date", "net_sales": "net_sales",
           "cost_of_goods_sold": "cost", "product": "product", "region": "region", "customer": "customer"}
results = []


def whoami():
    with b._connect(tenant, True) as c, c.cursor() as cur:
        cur.execute("SELECT current_user(), current_catalog()")
        u, cat = cur.fetchone()
        cur.execute("SHOW CATALOGS")
        cats = [r[0] for r in cur.fetchall()]
        if s.databricks_catalog not in cats:
            raise RuntimeError(f"catalog '{s.databricks_catalog}' not found; visible catalogs: {cats}")
        return f"user={u}, catalog={cat}"


try:
    results.append(step("1 connect, identify user, find catalog (Unity Catalog)", whoami)[0])
    results.append(step("2 load bronze table (batched parameterised inserts)",
                        lambda: b.load_bronze(tenant, "bronze_ref", df))[0])
    ok, res = step("3 build fact_sales + quarantine", lambda: b.build_fact_sales(tenant, "bronze_ref", mapping))
    results.append(ok and res["rows_loaded"] == 3 and res["rows_rejected"] == 0)

    def gross_profit():
        plan = Plan(intent="metric_total", metric="gross_profit", visualization="number")
        sql, params = compile_plan(plan, "net_sales - cost_of_goods_sold", "sum", month_expr=b.month_expr("order_date"))
        validate_select(sql, b.dialect)
        cols, rows = b.query(tenant, sql, params)
        v = float(rows[0][0])
        if v != 140:
            raise AssertionError(f"expected 140, got {v}")
        return f"gross profit = {v:g}"

    who = "read-only token" if s.databricks_readonly_token else "write token (no readonly token set)"
    results.append(step(f"4 governed query via {who}", gross_profit)[0])

    def monthly():
        plan = Plan(intent="compare_metric_over_time", metric="net_revenue", dimensions=["month"],
                    visualization="line_chart")
        sql, params = compile_plan(plan, "net_sales", "sum", month_expr=b.month_expr("order_date"))
        validate_select(sql, b.dialect)
        _, rows = b.query(tenant, sql, params)
        return f"{len(rows)} month rows: {[[r[0], float(r[1])] for r in rows]}"

    results.append(step("5 monthly trend query (Databricks date_format)", monthly)[0])
    results.append(step("6 scalar helper (freshness)", lambda: str(b.scalar(tenant, "SELECT max(order_date) FROM fact_sales")))[0])
finally:
    def cleanup():
        with b._connect(tenant, True) as c, c.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    step("cleanup: drop temporary schema", cleanup)

print("\nALL PASSED" if all(results) else "\nSOME STEPS FAILED - send me the lines above (no secrets are printed).")
