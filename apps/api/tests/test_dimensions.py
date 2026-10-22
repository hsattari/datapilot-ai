"""Products/customers dimensions: joins, unassigned handling, duplicate keys, ordering, isolation, SQL validation."""
import pandas as pd
import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.services.backends import DatabricksBackend
from app.services.ingestion import run_pending
from app.services.query_engine import QueryRejected, validate_select

from .conftest import DATA, ingest_and_model, register
from .test_databricks_backend import TENANT, dbx  # noqa: F401  (fixture)


def upload(client, h, name, data=None):
    """Upload a file as its own source; return the id of the dataset it produced."""
    sid = client.post("/api/v1/data-sources", json={"name": name}, headers=h).json()["id"]
    content = data if data is not None else (DATA / name).read_bytes()
    r = client.post(f"/api/v1/data-sources/{sid}/uploads", headers=h, files={"file": (name, content, "text/csv")})
    assert r.status_code == 202, r.text
    with SessionLocal() as db:
        run_pending(db)
    assert client.get(f"/api/v1/jobs/{r.json()['job_id']}", headers=h).json()["status"] == "succeeded"
    stem = name.rsplit(".", 1)[0]
    return next(d["id"] for d in client.get("/api/v1/datasets", headers=h).json() if d["name"] == stem)


def approve_dim(client, h, ds):
    prof = client.get(f"/api/v1/datasets/{ds}/profile", headers=h).json()["profile"]
    sug = prof["suggested_dimension"]
    assert sug, prof
    r = client.post(f"/api/v1/datasets/{ds}/approve", headers=h, json={"mapping": sug["mapping"], "role": sug["role"]})
    assert r.status_code == 200, r.text
    return sug, r.json()


def ask(client, h, q):
    return client.post("/api/v1/analytics/query", json={"question": q}, headers=h).json()


def setup_sales(client, h):
    ds = upload(client, h, "orders_reference.csv")
    prof = client.get(f"/api/v1/datasets/{ds}/profile", headers=h).json()["profile"]
    mapping = {k: v["column"] for k, v in prof["suggested_mapping"].items()}
    assert client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h).status_code == 200
    assert client.post("/api/v1/metrics/gross_profit/approve", headers=h).status_code == 200


def test_files_are_recognised_by_shape(client):
    h, _ = register(client, "Shape", "d@shape.test")
    ds, _ = ingest_and_model(client, h)
    sales = client.get(f"/api/v1/datasets/{ds}/profile", headers=h).json()["profile"]
    assert sales["suggested_dimension"] is None  # a sales file with product/customer columns is still a fact table
    p = upload(client, h, "products.csv")
    c = upload(client, h, "customers.csv")
    sp = client.get(f"/api/v1/datasets/{p}/profile", headers=h).json()["profile"]["suggested_dimension"]
    sc = client.get(f"/api/v1/datasets/{c}/profile", headers=h).json()["profile"]["suggested_dimension"]
    assert sp["role"] == "dim_product" and sp["mapping"] == {"product": "product", "category": "category"}
    assert sc["role"] == "dim_customer" and sc["mapping"] == {"customer": "customer_id", "segment": "segment"}


def test_revenue_and_profit_by_category_and_segment(client):
    h, _ = register(client, "Joins", "d@joins.test")
    setup_sales(client, h)
    _, res = approve_dim(client, h, upload(client, h, "products.csv"))
    assert res["rows_loaded"] == 2 and res["rows_rejected"] == 0
    approve_dim(client, h, upload(client, h, "customers.csv"))

    a = ask(client, h, "What is net revenue by category?")
    assert a["outcome"] == "answered", a
    assert sorted(a["rows"]) == [["Accessories", 200], ["Hardware", 150]]
    assert "LEFT JOIN dim_product" in a["sql"]
    assert sum(r[1] for r in a["rows"]) == 350  # joins never change the total

    a = ask(client, h, "gross profit by category")
    assert sorted(a["rows"]) == [["Accessories", 70], ["Hardware", 70]]
    assert sum(r[1] for r in a["rows"]) == 140

    a = ask(client, h, "net revenue by segment")
    assert sorted(a["rows"]) == [["Retail", 150], ["Wholesale", 200]]

    rels = client.get("/api/v1/relationships", headers=h).json()["relationships"]
    assert {r["role"]: r["key_coverage"] for r in rels} == {"dim_product": 1.0, "dim_customer": 1.0}
    assert all(r["status"] == "pass" and r["rows_unassigned"] == 0 for r in rels)


def test_orphans_are_unassigned_and_duplicate_keys_never_fan_out(client):
    h, _ = register(client, "Messy", "d@messy.test")
    setup_sales(client, h)
    messy = b"product,category\nWidget,Hardware\nWidget,Hardware\n,Nothing\n"  # duplicate key, blank key, Gadget missing
    ds = upload(client, h, "products.csv", messy)
    _, res = approve_dim(client, h, ds)
    assert (res["rows_loaded"], res["rows_rejected"]) == (1, 2)  # loaded + quarantined == staged
    a = ask(client, h, "net revenue by category")
    assert sorted(a["rows"]) == [["Hardware", 150], ["Unassigned", 200]]
    assert sum(r[1] for r in a["rows"]) == 350  # no fan-out from the duplicate, nothing dropped for the orphan
    rel = client.get("/api/v1/relationships", headers=h).json()["relationships"][0]
    assert rel["orphan_keys"] == 1 and rel["key_coverage"] == 0.5 and rel["rows_unassigned"] == 1
    q = {r["rule"]: r for r in client.get(f"/api/v1/datasets/{ds}/quality", headers=h).json()["results"]}
    assert q["key_unique"]["status"] == "warn" and q["join_coverage"]["status"] == "warn"


def test_dimension_approved_before_sales_is_checked_once_sales_exist(client):
    h, _ = register(client, "Order", "d@order.test")
    ds = upload(client, h, "products.csv")
    approve_dim(client, h, ds)
    assert client.get("/api/v1/relationships", headers=h).json()["relationships"][0]["sales_modelled"] is False
    setup_sales(client, h)
    rel = client.get("/api/v1/relationships", headers=h).json()["relationships"][0]
    assert rel["sales_modelled"] and rel["key_coverage"] == 1.0
    q = {r["rule"] for r in client.get(f"/api/v1/datasets/{ds}/quality", headers=h).json()["results"]}
    assert "join_coverage" in q


def test_category_without_a_dimension_is_refused_clearly(client):
    h, _ = register(client, "None", "d@none.test")
    setup_sales(client, h)
    a = ask(client, h, "net revenue by category")
    assert a["outcome"] == "refusal" and "products" in a["message"].lower()


def test_dimensions_are_tenant_isolated(client):
    h1, _ = register(client, "One", "d@one.test")
    h2, _ = register(client, "Two", "d@two.test")
    setup_sales(client, h1)
    approve_dim(client, h1, upload(client, h1, "products.csv"))
    setup_sales(client, h2)
    assert ask(client, h2, "net revenue by category")["outcome"] == "refusal"  # tenant two never sees one's products
    assert ask(client, h1, "net revenue by category")["outcome"] == "answered"


def test_invalid_dimension_mapping_rejected(client):
    h, _ = register(client, "Bad", "d@bad.test")
    ds = upload(client, h, "products.csv")
    r = client.post(f"/api/v1/datasets/{ds}/approve", headers=h,
                    json={"role": "dim_product", "mapping": {"product": "product", "net_sales": "list_price"}})
    assert r.status_code == 422
    r = client.post(f"/api/v1/datasets/{ds}/approve", headers=h, json={"role": "dim_product", "mapping": {"product": "product"}})
    assert r.status_code == 422  # attribute column is required


JOIN_OK = ("SELECT COALESCE(p.category, 'Unassigned') AS category, SUM(net_sales) AS value FROM fact_sales f "
           "LEFT JOIN dim_product p ON f.product = p.product_key GROUP BY 1 LIMIT 10")


@pytest.mark.parametrize("sql", [
    JOIN_OK.replace("LEFT JOIN", "INNER JOIN"),
    JOIN_OK.replace("LEFT JOIN", "CROSS JOIN").replace("ON f.product = p.product_key", ""),
    JOIN_OK.replace("dim_product", "users"),
    JOIN_OK.replace("ON f.product = p.product_key", "USING (product)"),
    JOIN_OK.replace("ON f.product = p.product_key", "ON f.product > p.product_key"),
    JOIN_OK.replace("LIMIT 10", "UNION SELECT 1, 2 LIMIT 1"),
    "SELECT category, SUM(net_sales) FROM fact_sales f LEFT JOIN (SELECT * FROM dim_product) p ON 1=1 LIMIT 5",
])
def test_validator_rejects_unsafe_joins(sql):
    with pytest.raises(QueryRejected):
        validate_select(sql)


def test_validator_accepts_the_generated_join():
    validate_select(JOIN_OK)


def test_filter_value_is_bound_not_interpolated(client):
    h, _ = register(client, "Inj", "d@inj.test")
    setup_sales(client, h)
    approve_dim(client, h, upload(client, h, "products.csv"))
    from app.services.query_engine import Filter, Plan, compile_plan
    plan = Plan(intent="metric_breakdown", metric="net_revenue", dimensions=["category"],
                filters=[Filter(field="category", value="x' OR '1'='1")])
    sql, params = compile_plan(plan, "net_sales", "sum", available_tables={"fact_sales", "dim_product"})
    assert "x' OR" not in sql and params == ["x' OR '1'='1"]
    validate_select(sql)


def test_databricks_dimension_sql_is_pinned_and_reconciled(dbx):  # noqa: F811
    b, log, conns, results = dbx
    results += [2, 0, 2, 0]  # loaded, rejected, staged, attribute-missing
    out = b.build_dimension(TENANT, "bronze_x", "dim_product", {"product": "product", "category": "category"})
    assert conns[-1]["access_token"] == "WRITE" and "schema" not in conns[-1]
    sql = " ".join(s for s, _ in log)
    assert "USE SCHEMA t_123e4567e89b12d3a456426614174000" in sql and "row_number()" in sql
    assert "CREATE OR REPLACE TABLE dim_product" in sql and "quarantine_dim_product" in sql
    assert out["rows_loaded"] == 2 and out["role"] == "dim_product"
    with pytest.raises(ValueError):
        b.build_dimension(TENANT, "bronze_x", "dim_product", {"product": "p; DROP"})
