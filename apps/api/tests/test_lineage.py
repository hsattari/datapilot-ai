"""Lineage: every link is read from real records, scoped to the organisation, and honest about what is unmapped."""
import hashlib

from .conftest import DATA, ingest_and_model, register
from .test_dimensions import approve_dim, upload


def _sales(client, h, name="orders_reference.csv", data=None):
    ds, mapping = ingest_and_model(client, h, name, data=data)
    assert client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h).status_code == 200
    return ds, mapping


def test_gross_profit_is_traced_column_by_column_to_the_uploaded_file(client):
    h, _ = register(client, "Lin", "l@lin.test")
    _sales(client, h)
    r = client.get("/api/v1/lineage/metrics/gross_profit", headers=h)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["metric"]["expression"] == "net_sales - cost_of_goods_sold" and t["complete"] is True
    cols = {c["field"]: c for c in t["columns"]}
    assert cols["net_sales"]["source_column"] == "net_sales" and cols["cost_of_goods_sold"]["source_column"] == "cost"
    f = cols["cost_of_goods_sold"]["file"]
    assert f["filename"] == "orders_reference.csv"
    assert f["sha256"] == hashlib.sha256((DATA / "orders_reference.csv").read_bytes()).hexdigest()   # the exact upload
    fact = next(x for x in t["models"] if x["role"] == "fact_sales")
    assert fact["built"] is True and fact["rows"] == 3


def test_unmapped_inputs_are_reported_not_hidden(client):
    h, _ = register(client, "NoCost", "n@lin.test")
    _sales(client, h, "s.csv", b"order_id,order_date,net_sales\nA,2026-01-01,10\n")
    t = client.get("/api/v1/lineage/metrics/gross_profit", headers=h).json()
    cost = next(c for c in t["columns"] if c["field"] == "cost_of_goods_sold")
    assert cost["mapped"] is False and cost["source_column"] is None and cost["file"] is None
    assert t["complete"] is False and "not mapped" in t["note"]
    assert client.get("/api/v1/lineage/metrics/net_revenue", headers=h).json()["complete"] is True


def test_dimension_tables_appear_for_metrics_that_can_use_them(client):
    h, _ = register(client, "Dim", "d@lin.test")
    _sales(client, h)
    ds = upload(client, h, "products.csv")
    approve_dim(client, h, ds)
    t = client.get("/api/v1/lineage/metrics/net_revenue", headers=h).json()
    prod = next(x for x in t["models"] if x["role"] == "dim_product")
    assert prod["built"] and prod["datasets"][0]["file"]["filename"] == "products.csv"


def test_reports_alerts_and_answers_are_listed_as_users_of_the_metric(client):
    h, _ = register(client, "Use", "u@lin.test")
    _sales(client, h)
    assert client.post("/api/v1/metrics/gross_profit/approve", headers=h).status_code == 200
    q = client.post("/api/v1/analytics/query", json={"question": "gross profit"}, headers=h).json()
    assert q["outcome"] == "answered"
    assert client.post("/api/v1/reports", json={"title": "GP", "question": "gross profit"}, headers=h).status_code == 201
    assert client.post("/api/v1/alerts", json={"name": "GP high", "metric": "gross_profit", "comparison": "above",
                                               "threshold": 100}, headers=h).status_code == 201
    u = client.get("/api/v1/lineage/metrics/gross_profit", headers=h).json()["used_by"]
    assert [r["title"] for r in u["reports"]] == ["GP"] and [a["name"] for a in u["alerts"]] == ["GP high"]
    assert u["answers_last_30_days"] >= 1


def test_graph_is_connected_and_every_edge_points_at_a_real_node(client):
    h, _ = register(client, "Graph", "g@lin.test")
    _sales(client, h)
    g = client.get("/api/v1/lineage", headers=h).json()
    ids = {n["id"] for n in g["nodes"]}
    assert g["edges"] and all(e["from"] in ids and e["to"] in ids for e in g["edges"])
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"source", "file", "dataset", "model", "metric"} <= kinds
    pairs = {(e["from"].split(":")[0], e["to"].split(":")[0]) for e in g["edges"]}
    assert {("source", "file"), ("file", "dataset"), ("dataset", "model"), ("model", "metric")} <= pairs


def test_lineage_is_tenant_scoped_and_unknown_metrics_are_404(client):
    h1, _ = register(client, "One", "1@lin.test")
    h2, _ = register(client, "Two", "2@lin.test")
    _sales(client, h1)
    g2 = client.get("/api/v1/lineage", headers=h2).json()
    assert not [n for n in g2["nodes"] if n["kind"] in ("source", "file", "dataset", "model")]
    t2 = client.get("/api/v1/lineage/metrics/gross_profit", headers=h2).json()
    assert all(c["file"] is None for c in t2["columns"])                       # nothing from tenant one leaks
    assert client.get("/api/v1/lineage/metrics/not_a_metric", headers=h1).status_code == 404
    assert client.get("/api/v1/lineage").status_code == 401
