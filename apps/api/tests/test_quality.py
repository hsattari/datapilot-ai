from .conftest import ingest_and_model, register


def quality(client, h, ds):
    r = client.get(f"/api/v1/datasets/{ds}/quality", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def by_rule(q):
    return {(r["stage"], r["rule"]): r for r in q["results"]}


def test_clean_reference_data_passes_quality(client):
    h, _ = register(client, "Acme", "q@acme.test")
    ds, mapping = ingest_and_model(client, h)
    q = quality(client, h, ds)
    assert q["overall"] == "pass" and q["counts"]["fail"] == 0
    assert all(r["stage"] == "profile" for r in q["results"])  # model stage only after approval
    assert client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h).status_code == 200
    rules = by_rule(quality(client, h, ds))
    assert rules[("model", "reconciliation")]["status"] == "pass"
    assert rules[("model", "rejection_rate")]["status"] == "pass"
    assert rules[("model", "cost_coverage")]["status"] == "pass"


def test_bad_rows_are_reported_not_hidden(client):
    h, _ = register(client, "Messy", "q@messy.test")
    data = (b"order_id,order_date,net_sales,cost_of_goods_sold\n"
            b"A,2026-01-01,10,4\nB,2026-01-02,abc,4\nC,2026-01-03,-5,\nC,2026-01-03,-5,\n")
    ds, mapping = ingest_and_model(client, h, "messy.csv", data=data)
    client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h)
    q = quality(client, h, ds)
    rules = by_rule(q)
    assert q["overall"] == "fail"  # 1 of 4 rows quarantined = 25% > 10%
    assert rules[("model", "rejection_rate")]["status"] == "fail"
    assert rules[("model", "reconciliation")]["observed"] == {"loaded": 3, "rejected": 1}
    assert rules[("profile", "duplicate_rows")]["status"] == "warn"
    assert rules[("model", "negative_sales")]["status"] == "warn"
    assert rules[("model", "cost_coverage")]["status"] == "warn"
    assert rules[("model", "duplicate_order_ids")]["status"] == "warn"


def test_schema_versions_track_drift(client):
    h, _ = register(client, "Drift", "q@drift.test")
    sid = client.post("/api/v1/data-sources", json={"name": "S"}, headers=h).json()["id"]

    def up(content):
        r = client.post(f"/api/v1/data-sources/{sid}/uploads", headers=h,
                        files={"file": ("sales.csv", content, "text/csv")})
        assert r.status_code == 202
        from app.db import SessionLocal
        from app.services.ingestion import run_pending
        with SessionLocal() as db:
            run_pending(db)
        return [d["id"] for d in client.get("/api/v1/datasets", headers=h).json()]

    up(b"order_id,order_date,net_sales,region\nA,2026-01-01,10,N\n")
    ids = up(b"order_id,order_date,net_sales,channel\nA,2026-01-01,10,web\n")
    assert len(ids) == 2
    newest = None
    for i in ids:
        v = client.get(f"/api/v1/datasets/{i}/schema-versions", headers=h).json()["versions"]
        assert [x["version"] for x in v] == [1, 2]
        newest = v[-1]
    assert newest["changes"] == {"added": ["channel"], "removed": ["region"], "type_changed": []}
    q = {r["rule"]: r for r in quality(client, h, newest["dataset_id"])["results"]}
    assert q["schema_drift"]["status"] == "warn"


def test_quality_is_tenant_isolated(client):
    h1, _ = register(client, "One", "q@one.test")
    h2, _ = register(client, "Two", "q@two.test")
    ds, _ = ingest_and_model(client, h1)
    assert client.get(f"/api/v1/datasets/{ds}/quality", headers=h2).status_code == 404
    assert client.get(f"/api/v1/datasets/{ds}/schema-versions", headers=h2).status_code == 404
