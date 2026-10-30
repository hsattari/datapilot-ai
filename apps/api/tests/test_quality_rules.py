"""Per-organisation data-quality thresholds: validated, admin-only, applied to new results, isolated, audited."""
from .conftest import ingest_and_model, register
from .test_insights import csv
from .test_workspace import add_viewer

ROWS = [("A", "2026-01-10", 100, 60, "W"), ("B", "2026-01-11", -20, 10, "W"), ("B", "2026-01-12", 50, 30, "X")]   # one negative, one repeated id


def results(client, h, ds):
    return {r["rule"]: r for r in client.get(f"/api/v1/datasets/{ds}/quality", headers=h).json()["results"]} \
        if "results" in client.get(f"/api/v1/datasets/{ds}/quality", headers=h).json() else \
        {r["rule"]: r for r in client.get(f"/api/v1/datasets/{ds}/quality", headers=h).json()}


def build(client, h, rows=ROWS):
    ds, mapping = ingest_and_model(client, h, "q.csv", data=csv(rows))
    assert client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h).status_code == 200
    return ds


def test_defaults_and_defaults_behaviour_unchanged(client):
    h, _ = register(client, "Q1", "q1@qr.test")
    body = client.get("/api/v1/quality-rules", headers=h).json()
    assert body["settings"] == body["defaults"] and body["settings"]["stale_days"] == 90
    r = results(client, h, build(client, h))
    assert r["negative_sales"]["status"] == "warn" and r["duplicate_order_ids"]["status"] == "warn"


def test_severity_can_be_raised_or_switched_off_and_applies_to_new_models(client):
    h, _ = register(client, "Q2", "q2@qr.test")
    assert client.put("/api/v1/quality-rules", headers=h, json={"negative_sales": "off", "duplicate_order_ids": "fail"}).status_code == 200
    r = results(client, h, build(client, h))
    assert "negative_sales" not in r                           # switched off: not reported at all
    assert r["duplicate_order_ids"]["status"] == "fail"


def test_stale_days_changes_the_freshness_message(client):
    h, _ = register(client, "Q3", "q3@qr.test")
    client.put("/api/v1/quality-rules", headers=h, json={"stale_days": 5})
    f = results(client, h, build(client, h))["freshness"]
    assert f["status"] == "warn" and "older than 5 days" in f["message"]


def test_nonsense_is_refused_with_the_reason(client):
    h, _ = register(client, "Q4", "q4@qr.test")
    for bad in ({"null_warn": 0.6, "null_fail": 0.5}, {"null_fail": 1.5}, {"reject_fail": 0}, {"stale_days": 0},
                {"negative_sales": "ignore"}, {"surprise": 1}, {"null_warn": "lots"}):
        r = client.put("/api/v1/quality-rules", headers=h, json=bad)
        assert r.status_code == 422 and r.json()["detail"], bad
    assert client.get("/api/v1/quality-rules", headers=h).json()["settings"]["stale_days"] == 90


def test_admin_only_isolated_and_audited(client):
    h, tid = register(client, "Q5", "q5@qr.test")
    analyst = add_viewer(tid, "a@qr.test", "analyst")
    assert client.put("/api/v1/quality-rules", headers=analyst, json={"stale_days": 10}).status_code == 403
    assert client.get("/api/v1/quality-rules", headers=analyst).status_code == 200
    client.put("/api/v1/quality-rules", headers=h, json={"stale_days": 10})
    h2, _ = register(client, "Q6", "q6@qr.test")
    assert client.get("/api/v1/quality-rules", headers=h2).json()["settings"]["stale_days"] == 90
    ev = client.get("/api/v1/activity", headers=h).json()
    assert "quality_rules.updated" in str(ev)


def test_completeness_limits_apply_to_the_profile_checks(client):
    h, _ = register(client, "Q7", "q7@qr.test")
    rows = [(f"O{i}", "2026-01-10", 10, 5, "" if i % 2 else "W") for i in range(10)]    # product blank in 50%
    client.put("/api/v1/quality-rules", headers=h, json={"null_warn": 0.6, "null_fail": 0.9})
    ds, _m = ingest_and_model(client, h, "c.csv", data=csv(rows))
    prof = client.get(f"/api/v1/datasets/{ds}/quality", headers=h).json()
    text = str(prof)
    assert "completeness:product" not in text            # 50% blank is below the raised warning limit
    h2, _ = register(client, "Q8", "q8@qr.test")            # same data, default limits: the check is reported
    ds2, _m = ingest_and_model(client, h2, "c.csv", data=csv(rows))
    assert "completeness:product" in str(client.get(f"/api/v1/datasets/{ds2}/quality", headers=h2).json())
