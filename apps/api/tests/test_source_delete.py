"""Deleting a data source removes its files and what was built from them, and nothing else."""
from app.db import SessionLocal
from app.services.backends import get_backend
from app.services.ingestion import run_pending
from app.services.storage import get_storage

from .conftest import ingest_and_model, register
from .test_insights import csv
from .test_security import hdr, login

A = [("A1", "2026-01-10", 100, 60, "Widget"), ("A2", "2026-01-11", 200, 130, "Gadget")]
B = [("B1", "2026-02-10", 50, 20, "Widget")]


def _upload(client, h, name, rows):
    sid = client.post("/api/v1/data-sources", json={"name": name}, headers=h).json()["id"]
    r = client.post(f"/api/v1/data-sources/{sid}/uploads", headers=h, files={"file": (name, csv(rows), "text/csv")})
    assert r.status_code == 202, r.text
    with SessionLocal() as db:
        run_pending(db)
    return sid


def _approve_all(client, h):
    for d in client.get("/api/v1/datasets", headers=h).json():
        if d["model_status"] == "approved":
            continue
        prof = client.get(f"/api/v1/datasets/{d['id']}/profile", headers=h).json()["profile"]
        sd = prof.get("suggested_dimension")
        if sd:
            body = {"mapping": sd["mapping"], "role": sd["role"]}
        else:
            body = {"mapping": {k: v["column"] for k, v in prof["suggested_mapping"].items()}}
        r = client.post(f"/api/v1/datasets/{d['id']}/approve", json=body, headers=h)
        assert r.status_code == 200, r.text


def _fact_rows(tid):
    return get_backend().scalar(tid, "SELECT count(*) FROM fact_sales")


def test_delete_removes_files_tables_and_records(client):
    h, tid = register(client, "Del", "d@del.test")
    sid = _upload(client, h, "a.csv", A)
    _approve_all(client, h)
    assert _fact_rows(tid) == 2   # modelled
    r = client.delete(f"/api/v1/data-sources/{sid}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] and body["files"] == 1 and body["datasets"] == 1 and body["sales_rows_removed"] == 2
    assert client.get("/api/v1/data-sources", headers=h).json() == []
    assert client.get("/api/v1/datasets", headers=h).json() == []
    assert _fact_rows(tid) == 0
    assert not any(t.startswith("bronze_") for t in get_backend().tables(tid))
    assert get_storage().delete_prefix(f"tenants/{tid}/{sid}/") == 0           # raw copy already gone
    ev = [e for e in client.get("/api/v1/activity", headers=h).json() if e["action"] == "source.deleted"]
    assert ev and ev[0]["detail"]["name"] == "a.csv" and "rows" not in str(ev[0]["detail"]).replace("sales_rows_removed", "")


def test_delete_one_of_two_sales_sources_keeps_the_other(client):
    h, tid = register(client, "Two", "t@two.test")
    sa = _upload(client, h, "a.csv", A)
    _upload(client, h, "b.csv", B)
    _approve_all(client, h)
    assert _fact_rows(tid) == 3
    assert client.delete(f"/api/v1/data-sources/{sa}", headers=h).status_code == 200
    assert _fact_rows(tid) == 1
    assert get_backend().scalar(tid, "SELECT order_id FROM fact_sales") == "B1"
    assert [s["name"] for s in client.get("/api/v1/data-sources", headers=h).json()] == ["b.csv"]


def test_other_organisation_cannot_delete(client):
    h1, tid = register(client, "One", "o@one.test")
    sid = _upload(client, h1, "a.csv", A)
    _approve_all(client, h1)
    h2, _ = register(client, "Other", "o@other.test")
    assert client.delete(f"/api/v1/data-sources/{sid}", headers=h2).status_code == 404
    assert _fact_rows(tid) == 2


def test_viewer_cannot_delete_and_busy_source_is_refused(client):
    h, tid = register(client, "Role", "r@role.test")
    sid = client.post("/api/v1/data-sources", json={"name": "q.csv"}, headers=h).json()["id"]
    assert client.post(f"/api/v1/data-sources/{sid}/uploads", headers=h,
                       files={"file": ("q.csv", csv(A), "text/csv")}).status_code == 202   # queued, not yet processed
    assert client.delete(f"/api/v1/data-sources/{sid}", headers=h).status_code == 409
    with SessionLocal() as db:
        run_pending(db)
    assert client.post("/api/v1/users", json={"email": "v@role.test", "role": "viewer", "password": "viewer-pass-12"}, headers=h).status_code == 201
    v = hdr(login(client, "v@role.test", "viewer-pass-12"))
    assert client.delete(f"/api/v1/data-sources/{sid}", headers=v).status_code == 403
    assert client.delete(f"/api/v1/data-sources/{sid}", headers=h).status_code == 200


def test_deleting_a_product_list_rebuilds_from_an_earlier_one(client):
    h, tid = register(client, "Dim", "d@dim.test")
    ds, _ = ingest_and_model(client, h, "orders_reference.csv")   # sales first
    sp1 = _upload_raw(client, h, "p1.csv", b"product_id,category\nW,Hardware\n")
    sp2 = _upload_raw(client, h, "p2.csv", b"product_id,category\nW,Accessories\n")
    _approve_all(client, h)
    first = get_backend().scalar(tid, "SELECT category FROM dim_product")
    assert first == "Accessories"                        # latest approved list wins
    assert client.delete(f"/api/v1/data-sources/{sp2}", headers=h).status_code == 200
    assert get_backend().scalar(tid, "SELECT category FROM dim_product") == "Hardware"   # rebuilt from the remaining list
    assert client.delete(f"/api/v1/data-sources/{sp1}", headers=h).status_code == 200
    assert "dim_product" not in get_backend().tables(tid)


def _upload_raw(client, h, name, content):
    sid = client.post("/api/v1/data-sources", json={"name": name}, headers=h).json()["id"]
    assert client.post(f"/api/v1/data-sources/{sid}/uploads", headers=h,
                       files={"file": (name, content, "text/csv")}).status_code == 202
    with SessionLocal() as db:
        run_pending(db)
    return sid
