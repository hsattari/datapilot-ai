"""Build the retail dbt template against the reference data in a throwaway DuckDB and verify results.

    pip install dbt-duckdb duckdb
    python dbt/check_template.py

Checks: dbt build passes; gross profit = 140; then a negative control: a bad date goes to quarantine with counts
reconciled, and a duplicate order_id makes the uniqueness test FAIL (proving the tests can fail).
"""
import pathlib
import subprocess
import sys
import tempfile

import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "dbt" / "templates" / "retail"
CSV = ROOT / "data" / "synthetic" / "orders_reference.csv"
PRODUCTS = ROOT / "data" / "synthetic" / "products.csv"
CUSTOMERS = ROOT / "data" / "synthetic" / "customers.csv"


def dbt(profiles: pathlib.Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["dbt", *args, "--profiles-dir", str(profiles), "--project-dir", str(TEMPLATE),
                           "--target-path", str(profiles / "target"), "--log-path", str(profiles / "logs")],
                          capture_output=True, text=True)


with tempfile.TemporaryDirectory() as d:
    d = pathlib.Path(d)
    db = d / "t.duckdb"
    (d / "profiles.yml").write_text(f"datapilot:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: {db}\n")
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA raw_sales")
    con.execute(f"CREATE TABLE raw_sales.orders AS SELECT * FROM read_csv('{CSV}', all_varchar=true)")
    con.execute(f"CREATE TABLE raw_sales.products AS SELECT * FROM read_csv('{PRODUCTS}', all_varchar=true)")
    con.execute(f"CREATE TABLE raw_sales.customers AS SELECT * FROM read_csv('{CUSTOMERS}', all_varchar=true)")
    con.close()

    r = dbt(d, "build")
    print(r.stdout[-600:])
    assert r.returncode == 0, "dbt build failed:\n" + r.stdout[-2000:] + r.stderr[-500:]
    con = duckdb.connect(str(db), read_only=True)
    gp = con.execute("select sum(net_sales - cost_of_goods_sold) from fact_sales").fetchone()[0]
    assert float(gp) == 140, f"gross profit {gp}"
    print("OK  dbt build passed, gross profit = 140")
    by_cat = dict(con.execute("select coalesce(p.category,'Unassigned'), sum(net_sales) from fact_sales f "
                              "left join dim_product p on f.product = p.product_key group by 1").fetchall())
    assert {k: float(v) for k, v in by_cat.items()} == {"Hardware": 150, "Accessories": 200}, by_cat
    print("OK  revenue by category via the dimension join: Hardware 150, Accessories 200")
    con.close()

    con = duckdb.connect(str(db))
    con.execute("INSERT INTO raw_sales.orders VALUES ('D','not-a-date','W','N','C9','10','5')")
    con.close()
    r = dbt(d, "build")
    assert r.returncode == 0, "bad date row should be quarantined, not fail the build:\n" + r.stdout[-1500:]
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("select count(*) from quarantine_sales").fetchone()[0] == 1
    assert con.execute("select count(*) from fact_sales").fetchone()[0] == 3
    con.close()
    print("OK  bad date quarantined; counts reconcile")

    con = duckdb.connect(str(db))
    con.execute("INSERT INTO raw_sales.products VALUES ('Widget','Hardware','10'), (' ','Blank','1')")  # repeated + blank key
    con.execute("INSERT INTO raw_sales.orders VALUES ('E','2026-03-05','Gizmo','N','C1','50','20')")  # product not in the list
    con.close()
    r = dbt(d, "build")
    assert r.returncode == 0, "orphans and repeated keys must warn/quarantine, not fail the build:\n" + r.stdout[-1500:]
    assert "WARN" in r.stdout and "relationships" in r.stdout, "orphan product must raise a relationships warning"
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("select count(*) from dim_product").fetchone()[0] == 2
    assert con.execute("select count(*) from quarantine_dim_product").fetchone()[0] == 2
    tot = con.execute("select sum(net_sales) from fact_sales f left join dim_product p on f.product = p.product_key").fetchone()[0]
    assert float(tot) == float(con.execute("select sum(net_sales) from fact_sales").fetchone()[0]), "join changed the total"
    con.close()
    print("OK  repeated/blank keys quarantined, orphan product warns, join keeps totals")

    con = duckdb.connect(str(db))
    con.execute("INSERT INTO raw_sales.orders VALUES ('A','2026-03-01','W','N','C1','10','5')")
    con.close()
    r = dbt(d, "build")
    assert r.returncode != 0 and "unique_fact_sales_order_id" in r.stdout, "duplicate must fail the unique test"
    print("OK  duplicate order_id makes the unique test fail (tests can fail)")
print("ALL PASSED")
