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
    env = pathlib.Path(".env").resolve()
    print(f"Missing settings: {', '.join(missing)}")
    print(f"Reading: {env}  (the script reads .env from the folder you run it in)")
    if not env.exists():
        sys.exit("  .env does not exist there. Run from the repo root, and check it is not named .env.txt")
    raw = env.read_bytes()
    print(f"  size={len(raw)} bytes, NUL bytes={raw.count(bytes([0]))}, "
          f"UTF-8 BOM={raw[:3] == bytes([239, 187, 191])}, UTF-16 BOM={raw[:2] in (bytes([255, 254]), bytes([254, 255]))}")
    if raw.count(bytes([0])):
        print("  -> The file contains UTF-16 text (typical of PowerShell `>>` / `echo`). Re-save it as UTF-8 in Notepad "
              "(File > Save As > Encoding: UTF-8) or retype the lines in a text editor.")
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(env, encoding="utf-8-sig")
        print("  Variable names the parser sees (value lengths only):")
        for k, v in vals.items():
            print(f"    {k}: {len(v) if v else 0} chars")
    except ImportError:
        print("  (pip install python-dotenv to list parsed names)")
    sys.exit("Fix the .env as above, then rerun.")
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


tok = s.databricks_token
hints = []
if not tok.startswith("dapi"):
    hints.append("token does not start with 'dapi' - Databricks personal access tokens do. Did you paste something else "
                 "(an Azure/Entra token, a client secret, the token ID or the comment)?")
if tok != tok.strip() or any(c in tok for c in "\"' \t"):
    hints.append("token contains quotes or spaces - remove them (NAME=value, nothing else on the line)")
if len(tok) < 30:
    hints.append(f"token is only {len(tok)} characters - it looks truncated")
print(f"Token check: starts with 'dapi' = {tok.startswith('dapi')}, length = {len(tok)} (value not shown)")
for h in hints:
    print("  WARNING:", h)

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
    ok1 = step("1 connect, identify user, find catalog (Unity Catalog)", whoami)[0]
    results.append(ok1)
    if not ok1:
        print("\nStopped: nothing else can work until step 1 passes. Likely causes, in order:")
        print("  1. Token invalid, revoked, expired or not a personal access token (see warnings above). Generate a new one:")
        print("     Databricks > profile > Settings > Developer > Access tokens > Generate new token.")
        print("  2. Token scope too narrow. When generating, choose the 'sql' scope or 'Other APIs' (all APIs), not 'BI tools' only.")
        print("  3. Workspace admin has disabled personal access tokens (Admin settings > Advanced > Personal access tokens).")
        print("  4. Your user lacks 'Can use' on the SQL warehouse (SQL Warehouses > your warehouse > Permissions).")
        raise SystemExit(1)
    ok1 = True
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
        if not results or not results[0]:
            return "nothing created"
        with b._connect(tenant, True) as c, c.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    step("cleanup: drop temporary schema", cleanup)

print("\nALL PASSED" if all(results) else "\nSOME STEPS FAILED - send me the lines above (no secrets are printed).")
