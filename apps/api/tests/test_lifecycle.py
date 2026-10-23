"""Export, tenant deletion, user erasure and retention."""
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app import models as m
from app.config import get_settings
from app.db import Base, SessionLocal
from app.services import lifecycle, totp
from app.services.backends import get_backend

from .conftest import register
from .test_dimensions import approve_dim, setup_sales, upload
from .test_security import PW, enable_mfa
from .test_workspace import add_viewer


def zip_of(r):
    assert r.status_code == 200, r.text
    return zipfile.ZipFile(io.BytesIO(r.content))


def names(z):
    return set(z.namelist())


def test_export_contains_everything_and_no_secrets(client):
    h, t = register(client, "Export Ltd", "e@export.test")
    setup_sales(client, h)
    approve_dim(client, h, upload(client, h, "products.csv"))
    client.post("/api/v1/analytics/query", json={"question": "gross profit"}, headers=h)
    client.post("/api/v1/reports", json={"title": "P", "question": "gross profit"}, headers=h)
    z = zip_of(client.get("/api/v1/export/tenant", headers=h))
    n = names(z)
    assert {"manifest.json", "README.txt", "metadata/tenant.json", "metadata/users.json", "metadata/datasets.json",
            "metadata/metrics.json", "metadata/reports.json", "metadata/questions.json", "metadata/audit_events.json",
            "data/fact_sales.csv", "data/dim_product.csv"} <= n
    assert not any(x.startswith("raw/") for x in n)  # raw files only on request
    assert not any(x.startswith("data/bronze") for x in n)
    fact = z.read("data/fact_sales.csv").decode().splitlines()
    assert fact[0].startswith("order_id,order_date") and len(fact) == 4  # header + 3 rows
    assert json.loads(z.read("metadata/tenant.json"))["name"] == "Export Ltd"
    # nothing secret anywhere in the archive
    blob = b"".join(z.read(x) for x in n)
    assert b"password_hash" not in blob and b"mfa_secret" not in blob and b"$2b$" not in blob
    users = json.loads(z.read("metadata/users.json"))
    assert users[0]["email"] == "e@export.test" and set(users[0]) == {"id", "email", "role", "active", "mfa_enabled", "created_at"}
    # manifest hashes verify
    import hashlib
    man = json.loads(z.read("manifest.json"))
    for f in man["files"]:
        assert hashlib.sha256(z.read(f["path"])).hexdigest() == f["sha256"]


def test_export_with_raw_files_and_audit(client):
    h, _ = register(client, "Raw", "e@raw.test")
    setup_sales(client, h)
    z = zip_of(client.get("/api/v1/export/tenant?include_raw=true", headers=h))
    raw = [x for x in names(z) if x.startswith("raw/")]
    assert len(raw) == 1 and raw[0].endswith("orders_reference.csv")
    assert z.read(raw[0]).startswith(b"order_id,order_date")
    assert any(a["action"] == "tenant.exported" for a in client.get("/api/v1/activity", headers=h).json())


def test_export_neutralises_spreadsheet_formulas(client):
    h, _ = register(client, "Formula", "e@formula.test")
    evil = b"order_id,order_date,net_sales,customer,product\nA,2026-01-01,10,=HYPERLINK(\"http://evil\"),+cmd|calc\nB,2026-01-02,-5,@SUM(1),-x\n"
    ds = upload(client, h, "orders.csv", evil)
    prof = client.get(f"/api/v1/datasets/{ds}/profile", headers=h).json()["profile"]
    mapping = {k: v["column"] for k, v in prof["suggested_mapping"].items()}
    assert client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h).status_code == 200
    z = zip_of(client.get("/api/v1/export/tenant", headers=h))
    rows = z.read("data/fact_sales.csv").decode()
    assert "'=HYPERLINK" in rows and "'+cmd|calc" in rows and "'@SUM(1)" in rows and "'-x" in rows
    assert ",-5.00," in rows or ",-5," in rows  # negative NUMBERS are not altered
    assert lifecycle.csv_cell(-5) == -5 and lifecycle.csv_cell("normal") == "normal" and lifecycle.csv_cell("=1+1") == "'=1+1"


def test_export_is_admin_only_and_tenant_scoped(client):
    h1, t1 = register(client, "One Ltd", "e@one.test")
    h2, _ = register(client, "Two Ltd", "e@two.test")
    setup_sales(client, h1)
    assert client.get("/api/v1/export/tenant", headers=add_viewer(t1)).status_code == 403
    assert client.get("/api/v1/export/tenant").status_code == 401
    z = zip_of(client.get("/api/v1/export/tenant?include_raw=true", headers=h2))
    blob = b"".join(z.read(x) for x in names(z))
    assert b"One Ltd" not in blob and b"e@one.test" not in blob and b"order_id,order_date" not in blob
    assert "data/fact_sales.csv" not in names(z)


def test_export_rate_limited(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_export_per_minute", 2)
    h, _ = register(client, "Rate", "e@rate.test")
    codes = [client.get("/api/v1/export/tenant", headers=h).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# --------------------------------------------------------------- tenant deletion
def tenant_rows(tenant_id):
    """Row counts for this tenant in EVERY tenant-scoped table (derived from the schema, so new tables are covered)."""
    out = {}
    with SessionLocal() as db:
        for table in Base.metadata.sorted_tables:
            if "tenant_id" in table.c and table.name != "deletion_log":
                n = db.execute(text(f"SELECT count(*) FROM {table.name} WHERE tenant_id = :t"), {"t": tenant_id}).scalar()
                if n:
                    out[table.name] = n
    return out


def test_delete_requires_exact_name_and_password(client):
    h, _ = register(client, "Doomed Ltd", "d@doomed.test")
    assert client.post("/api/v1/tenant/delete", headers=h, json={"confirm_name": "doomed ltd", "password": PW}).status_code == 422
    assert client.post("/api/v1/tenant/delete", headers=h, json={"confirm_name": "Doomed Ltd", "password": "wrong-pass-1"}).status_code == 401
    assert client.get("/api/v1/settings", headers=h).status_code == 200  # still alive
    assert any(a["action"] == "tenant.delete_refused" for a in client.get("/api/v1/activity", headers=h).json())


def test_delete_needs_admin(client):
    h, t = register(client, "Admin Only", "d@adminonly.test")
    v = add_viewer(t)
    assert client.post("/api/v1/tenant/delete", headers=v, json={"confirm_name": "Admin Only", "password": PW}).status_code == 403


def test_delete_with_mfa_needs_a_code(client):
    h, _ = register(client, "Secure Ltd", "d@secure.test")
    secret, recovery = enable_mfa(client, h)
    body = {"confirm_name": "Secure Ltd", "password": PW}
    assert client.post("/api/v1/tenant/delete", headers=h, json=body).status_code == 401
    assert client.post("/api/v1/tenant/delete", headers=h, json={**body, "code": "000000"}).status_code == 401
    r = client.post("/api/v1/tenant/delete", headers=h, json={**body, "code": recovery[0]})  # a recovery code works once
    assert r.status_code == 200 and r.json()["deleted"] is True


def test_delete_erases_everything_and_only_that_tenant(client):
    h1, t1 = register(client, "Erase Me Ltd", "d@erase.test")
    h2, t2 = register(client, "Keep Me Ltd", "d@keep.test")
    for h in (h1, h2):
        setup_sales(client, h)
    approve_dim(client, h1, upload(client, h1, "products.csv"))
    client.post("/api/v1/reports", json={"title": "P", "question": "gross profit"}, headers=h1)
    client.post("/api/v1/alerts", json={"name": "a", "metric": "net_revenue", "comparison": "below", "threshold": 1}, headers=h1)
    client.post("/api/v1/knowledge/documents", json={"title": "Policy", "text": "Refunds are processed within 14 days."}, headers=h1)
    viewer = add_viewer(t1, "v@erase.test")
    with SessionLocal() as db:
        raw_dir = Path(get_settings().local_storage_path) / "tenants" / t1
        db_file = Path(get_settings().analytics_path) / f"{t1}.duckdb"
        assert raw_dir.exists() and db_file.exists()
        assert db.scalar(select(m.User).where(m.User.email == "v@erase.test")) is not None
    before = tenant_rows(t1)
    assert before.get("documents") and before.get("document_chunks") and before.get("datasets") and before.get("reports")

    r = client.post("/api/v1/tenant/delete", headers=h1, json={"confirm_name": "Erase Me Ltd", "password": PW})
    assert r.status_code == 200, r.text
    counts = r.json()["counts"]
    assert counts["raw_files"] == 2 and counts["analytical_files"] == 1 and counts["users"] == 2 and counts["datasets"] == 2

    # every tenant-scoped table is empty for the tenant; files are gone; the tenant row is gone
    assert tenant_rows(t1) == {}
    assert not raw_dir.exists() and not db_file.exists()
    with SessionLocal() as db:
        assert db.get(m.Tenant, t1) is None
        assert db.scalar(select(m.User).where(m.User.email.in_(["d@erase.test", "v@erase.test"]))) is None
        rec = db.scalars(select(m.DeletionRecord).where(m.DeletionRecord.tenant_id == t1)).one()
        flat = json.dumps(rec.counts) + rec.tenant_id + rec.requested_by
        assert "Erase Me" not in flat and "erase.test" not in flat  # the record holds no names or e-mail addresses
    # old sessions and credentials are useless
    assert client.get("/api/v1/settings", headers=h1).status_code in (401, 403)
    assert client.get("/api/v1/settings", headers=viewer).status_code in (401, 403)
    assert client.post("/api/v1/auth/login", json={"email": "d@erase.test", "password": PW}).status_code == 401
    # the other tenant is untouched and still answers questions
    a = client.post("/api/v1/analytics/query", json={"question": "gross profit"}, headers=h2).json()
    assert a["outcome"] == "answered" and a["rows"][0][0] == 140
    assert tenant_rows(t2).get("datasets") == 1
    assert (Path(get_settings().analytics_path) / f"{t2}.duckdb").exists()


def test_failed_external_delete_leaves_tenant_intact_for_retry(client, monkeypatch):
    h, t = register(client, "Retry Ltd", "d@retry.test")
    setup_sales(client, h)
    calls = {"n": 0}
    real = get_backend().drop_tenant

    def flaky(tenant_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("storage unavailable")
        return real(tenant_id)

    monkeypatch.setattr(get_backend(), "drop_tenant", flaky)
    body = {"confirm_name": "Retry Ltd", "password": PW}
    with pytest.raises(RuntimeError):
        client.post("/api/v1/tenant/delete", headers=h, json=body)
    assert tenant_rows(t).get("datasets") == 1  # database untouched
    assert client.get("/api/v1/settings", headers=h).status_code == 200
    assert client.post("/api/v1/tenant/delete", headers=h, json=body).status_code == 200  # retry succeeds
    assert tenant_rows(t) == {}


# --------------------------------------------------------------- user erasure
def test_erase_user_removes_identity_but_keeps_audit_trail(client):
    h, t = register(client, "Users Ltd", "u@users.test")
    client.post("/api/v1/users", headers=h, json={"email": "leaver@users.test", "role": "analyst", "password": "initial-pass-1"})
    uid = next(u["id"] for u in client.get("/api/v1/users", headers=h).json() if u["email"] == "leaver@users.test")
    tok = client.post("/api/v1/auth/login", json={"email": "leaver@users.test", "password": "initial-pass-1"}).json()["token"]
    assert client.delete(f"/api/v1/users/{uid}", headers=h).status_code == 204
    assert [u["email"] for u in client.get("/api/v1/users", headers=h).json()] == ["u@users.test"]
    assert client.post("/api/v1/auth/login", json={"email": "leaver@users.test", "password": "initial-pass-1"}).status_code == 401
    assert client.get("/api/v1/settings", headers={"Authorization": f"Bearer {tok}"}).status_code in (401, 403)  # old token no longer works
    with SessionLocal() as db:
        u = db.get(m.User, uid)
        assert u.email.endswith("@erased.invalid") and u.disabled and not u.mfa_enabled
        blob = json.dumps([e.detail for e in db.scalars(select(m.AuditEvent).where(m.AuditEvent.tenant_id == t))])
        assert "leaver@users.test" not in blob and "[erased]" in blob
    acts = [a["action"] for a in client.get("/api/v1/activity", headers=h).json()]
    assert "user.erased" in acts and "user.added" in acts  # the trail remains, without the e-mail address
    # the address can be registered again
    assert client.post("/api/v1/users", headers=h, json={"email": "leaver@users.test", "role": "viewer", "password": "initial-pass-2"}).status_code == 201


def test_erase_user_guards(client):
    h1, t1 = register(client, "G1", "g@g1.test")
    h2, _ = register(client, "G2", "g@g2.test")
    uid = next(u["id"] for u in client.get("/api/v1/users", headers=h1).json())  # the only user, and the only admin
    assert client.delete(f"/api/v1/users/{uid}", headers=h1).status_code == 422  # last administrator
    assert client.delete(f"/api/v1/users/{uid}", headers=h2).status_code == 404  # another tenant's user
    assert client.delete("/api/v1/users/does-not-exist", headers=h1).status_code == 404
    assert client.delete(f"/api/v1/users/{uid}", headers=add_viewer(t1)).status_code == 403
    assert client.delete(f"/api/v1/users/{uid}").status_code == 401


# --------------------------------------------------------------- retention
def test_retention_purges_only_expired_records(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "retention_queries_days", 30)
    monkeypatch.setattr(s, "retention_audit_days", 60)
    h, t = register(client, "Keep", "r@keep.test")
    old = datetime.now(timezone.utc) - timedelta(days=90)
    mid = datetime.now(timezone.utc) - timedelta(days=45)
    with SessionLocal() as db:
        uid = db.scalar(select(m.User.id).where(m.User.email == "r@keep.test"))
        db.add_all([m.AnalyticalQuery(tenant_id=t, user_id=uid, question="net revenue", outcome="answered"),
                    m.AnalyticalQuery(tenant_id=t, user_id=uid, question="old", outcome="answered", created_at=old),
                    m.AnalyticalQuery(tenant_id=t, user_id=uid, question="mid", outcome="answered", created_at=mid),
                    m.AuditEvent(tenant_id=t, action="old.event", detail={}, created_at=old),
                    m.AuditEvent(tenant_id=t, action="mid.event", detail={}, created_at=mid)])
        db.commit()
        out = lifecycle.purge_expired(db)
        qs = {q.question for q in db.scalars(select(m.AnalyticalQuery).where(m.AnalyticalQuery.tenant_id == t))}
        ev = {e.action for e in db.scalars(select(m.AuditEvent).where(m.AuditEvent.tenant_id == t))}
    assert out["questions"] == 2 and out["audit_events"] == 1   # 'old' and 'mid' questions; only the 'old' audit event
    assert "old" not in qs and "mid" not in qs and "net revenue" in qs
    assert "old.event" not in ev and "mid.event" in ev


def test_retention_zero_keeps_everything(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "retention_queries_days", 0)
    monkeypatch.setattr(s, "retention_audit_days", 0)
    h, t = register(client, "Forever", "r@forever.test")
    with SessionLocal() as db:
        db.add(m.AuditEvent(tenant_id=t, action="ancient", detail={}, created_at=datetime.now(timezone.utc) - timedelta(days=5000)))
        db.commit()
        assert lifecycle.purge_expired(db) == {}
        assert db.scalar(select(m.AuditEvent).where(m.AuditEvent.action == "ancient")) is not None
