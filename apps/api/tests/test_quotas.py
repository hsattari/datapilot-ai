"""Plan limits: enforced where cost arises, per organisation, with warnings before the limit and clear messages at it."""
import pytest
from sqlalchemy import select

from app import models as m
from app.db import SessionLocal
from app.services import quotas

from .conftest import ingest_and_model, register
from .test_dimensions import ask


@pytest.fixture
def tiny(monkeypatch):
    """A plan with very small limits, installed as 'starter'."""
    monkeypatch.setitem(quotas.PLANS, "starter", dict(uploads=2, rows=0, questions=3, users=2, documents=1))


def _plan(tenant_id, plan):
    with SessionLocal() as db:
        db.get(m.Tenant, tenant_id).plan = plan
        db.commit()


def _upload(client, h, name="a.csv", data=b"order_id,order_date,net_sales\nA,2026-01-01,10\n"):
    sid = client.post("/api/v1/data-sources", json={"name": name}, headers=h).json()["id"]
    return client.post(f"/api/v1/data-sources/{sid}/uploads", headers=h, files={"file": (name, data, "text/csv")})


def test_uploads_stop_at_the_limit_with_a_clear_message(client, tiny):
    h, tid = register(client, "Up", "u@up.test")
    _plan(tid, "starter")
    assert _upload(client, h, "a.csv").status_code == 202
    assert _upload(client, h, "b.csv").status_code == 202
    r = _upload(client, h, "c.csv")
    assert r.status_code == 429 and "Plan limit reached: 2 of 2 files uploaded" in r.json()["detail"]


def test_questions_stop_at_the_daily_limit_and_every_attempt_counts(client, tiny):
    h, tid = register(client, "Q", "q@q.test")
    _plan(tid, "starter")
    for _ in range(3):   # refused questions are logged too: every attempt costs something, so every attempt counts
        assert client.post("/api/v1/analytics/query", json={"question": "show me everyone's salary"}, headers=h).status_code == 200
    r = client.post("/api/v1/analytics/query", json={"question": "total revenue"}, headers=h)
    assert r.status_code == 429 and "questions asked in the last 24 hours" in r.json()["detail"]


def test_people_and_documents_are_capped(client, tiny):
    h, tid = register(client, "P", "p@p.test")
    _plan(tid, "starter")
    r = client.post("/api/v1/users", headers=h, json={"email": "b@p.test", "role": "viewer", "password": "initial-pass-2"})
    assert r.status_code == 201                                                  # 2nd person is allowed
    r = client.post("/api/v1/users", headers=h, json={"email": "c@p.test", "role": "viewer", "password": "initial-pass-2"})
    assert r.status_code == 429 and "people in the organisation" in r.json()["detail"]
    assert client.post("/api/v1/knowledge/documents", headers=h, json={"title": "t1", "text": "alpha beta gamma"}).status_code == 201
    r = client.post("/api/v1/knowledge/documents", headers=h, json={"title": "t2", "text": "delta epsilon zeta"})
    assert r.status_code == 429 and "knowledge documents" in r.json()["detail"]


def test_usage_reports_plan_limits_and_warnings_before_the_limit(client, tiny):
    h, tid = register(client, "W", "w@w.test")
    _plan(tid, "starter")
    _upload(client, h, "a.csv")                      # 1 of 2: 50%, no warning yet
    q = client.get("/api/v1/usage", headers=h).json()["quota"]
    assert q["plan"] == "starter" and q["limits"]["uploads"] == 2 and q["used"]["uploads"] == 1
    assert not [w for w in q["warnings"] if w["item"] == "uploads"]
    _upload(client, h, "b.csv")                      # 2 of 2: reached
    q = client.get("/api/v1/usage", headers=h).json()["quota"]
    w = [w for w in q["warnings"] if w["item"] == "uploads"][0]
    assert w["reached"] is True and "Limit reached" in w["message"]


def test_warning_appears_at_80_percent(client, monkeypatch):
    monkeypatch.setitem(quotas.PLANS, "starter", dict(uploads=5, rows=0, questions=0, users=0, documents=0))
    h, tid = register(client, "Eighty", "e@e.test")
    _plan(tid, "starter")
    for i in range(4):
        _upload(client, h, f"f{i}.csv")
    ws = client.get("/api/v1/usage", headers=h).json()["quota"]["warnings"]
    assert ws and ws[0]["item"] == "uploads" and ws[0]["reached"] is False and "4 of 5" in ws[0]["message"]


def test_unlimited_plan_is_never_blocked(client):
    h, tid = register(client, "Big", "b@big.test")           # test config gives 'enterprise'
    assert client.get("/api/v1/usage", headers=h).json()["quota"]["plan"] == "enterprise"
    for i in range(6):
        assert _upload(client, h, f"f{i}.csv").status_code == 202


def test_limits_are_per_organisation(client, tiny):
    h1, t1 = register(client, "One", "1@one.test")
    h2, t2 = register(client, "Two", "2@two.test")
    _plan(t1, "starter"); _plan(t2, "starter")
    _upload(client, h1, "a.csv"); _upload(client, h1, "b.csv")
    assert _upload(client, h1, "c.csv").status_code == 429
    assert _upload(client, h2, "a.csv").status_code == 202                       # the other organisation is unaffected


def test_a_customer_cannot_change_their_own_plan(client):
    h, tid = register(client, "Self", "s@self.test")
    r = client.patch("/api/v1/settings", json={"plan": "enterprise"}, headers=h)
    assert r.status_code in (200, 422)
    assert client.get("/api/v1/usage", headers=h).json()["quota"]["plan"] == "enterprise"   # unchanged (test default)
    with SessionLocal() as db:
        db.get(m.Tenant, tid).plan = "starter"; db.commit()
    client.patch("/api/v1/settings", json={"plan": "enterprise"}, headers=h)
    assert client.get("/api/v1/usage", headers=h).json()["quota"]["plan"] == "starter"      # the field is ignored
