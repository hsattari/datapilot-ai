"""Scheduled alert checks run by the worker: due alerts only, one event per state change, tenants kept apart."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import models as m
from app.db import SessionLocal
from app.services.alerts import run_due_alerts

from .conftest import ingest_and_model, register


def _setup(client, org, email):
    h, tid = register(client, org, email)
    ds, mapping = ingest_and_model(client, h)
    assert client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h).status_code == 200
    return h, tid


def _alert(client, h, threshold, comparison="above"):
    r = client.post("/api/v1/alerts", headers=h, json={"name": f"rev {comparison} {threshold}", "metric": "net_revenue",
                                                        "comparison": comparison, "threshold": threshold})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _events(tid, action):
    with SessionLocal() as db:
        return db.scalars(select(m.AuditEvent).where(m.AuditEvent.tenant_id == tid,
                                                      m.AuditEvent.action == action)).all()


def test_due_alert_is_checked_and_logged_once_per_state_change(client):
    h, tid = _setup(client, "Sched", "s@sched.test")
    aid = _alert(client, h, 100)                                  # net revenue is 350: above 100 -> triggered
    with SessionLocal() as db:
        assert run_due_alerts(db, interval_minutes=60) == 1
    a = next(x for x in client.get("/api/v1/alerts", headers=h).json() if x["id"] == aid)
    assert a["last_state"] == "triggered" and a["last_value"] == 350 and a["last_checked_at"]
    assert len(_events(tid, "alert.triggered")) == 1

    # not due again within the interval
    with SessionLocal() as db:
        assert run_due_alerts(db, interval_minutes=60) == 0

    # due again later, still triggered: checked, but no second event
    later = datetime.now(timezone.utc) + timedelta(minutes=61)
    with SessionLocal() as db:
        assert run_due_alerts(db, interval_minutes=60, now=later) == 1
    assert len(_events(tid, "alert.triggered")) == 1


def test_recovery_is_logged_when_the_alert_clears(client):
    h, tid = _setup(client, "Rec", "r@rec.test")
    aid = _alert(client, h, 100)
    with SessionLocal() as db:
        run_due_alerts(db, 60)
        a = db.get(m.Alert, aid)
        a.threshold = 1000                                        # revenue 350 is no longer above the threshold
        db.commit()
    later = datetime.now(timezone.utc) + timedelta(minutes=61)
    with SessionLocal() as db:
        run_due_alerts(db, 60, now=later)
    assert len(_events(tid, "alert.recovered")) == 1
    assert client.get("/api/v1/alerts", headers=h).json()[0]["last_state"] == "ok"


def test_alert_on_a_metric_that_is_no_longer_approved_becomes_error_without_stopping_others(client):
    h, tid = _setup(client, "Err", "e@err.test")
    bad = _alert(client, h, 100)
    good = _alert(client, h, 100, "below")
    with SessionLocal() as db:
        db.get(m.Alert, bad).metric = "no_such_metric"
        db.commit()
        assert run_due_alerts(db, 60) == 2
    states = {a["id"]: a["last_state"] for a in client.get("/api/v1/alerts", headers=h).json()}
    assert states[bad] == "error" and states[good] == "ok"


def test_each_organisation_is_checked_against_its_own_data(client):
    h1, t1 = _setup(client, "One", "1@one.test")
    h2, t2 = register(client, "Two", "2@two.test")               # no data at all
    a1 = _alert(client, h1, 100)
    with SessionLocal() as db:
        db.add(m.Alert(tenant_id=t2, name="other", metric="net_revenue", comparison="above", threshold=1,
                       created_by="x"))
        db.commit()
        run_due_alerts(db, 60)
    assert next(x for x in client.get("/api/v1/alerts", headers=h1).json() if x["id"] == a1)["last_value"] == 350
    other = client.get("/api/v1/alerts", headers=h2).json()[0]
    assert other["last_value"] is None and other["last_state"] == "error"   # never sees tenant one's 350
    assert len(_events(t2, "alert.triggered")) == 0


def test_on_demand_evaluation_still_works(client):
    h, tid = _setup(client, "Dem", "d@dem.test")
    _alert(client, h, 100)
    r = client.post("/api/v1/alerts/evaluate", headers=h).json()["alerts"]
    assert r[0]["last_state"] == "triggered"
    assert len(_events(tid, "alert.triggered")) == 1
