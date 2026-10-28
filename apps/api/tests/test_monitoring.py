"""Monitoring: failed refreshes, schema changes and missing files are reported once, resolve when fixed, and stay
inside the organisation; management summaries use real numbers and real events."""
from datetime import date, datetime, timedelta, timezone

from app.db import SessionLocal
from app.services import health, summaries
from app.services.ingestion import run_pending

from .conftest import register

GOOD = b"order_id,order_date,net_sales,cost\nA,2026-01-10,100,60\nB,2026-01-20,200,130\n"


def _source(client, h, name="Shop"):
    return client.post("/api/v1/data-sources", json={"name": name}, headers=h).json()["id"]


def _upload(client, h, sid, name, data):
    r = client.post(f"/api/v1/data-sources/{sid}/uploads", headers=h, files={"file": (name, data, "text/csv")})
    assert r.status_code == 202, r.text
    with SessionLocal() as db:
        run_pending(db)
    return client.get(f"/api/v1/jobs/{r.json()['job_id']}", headers=h).json()


def _health(client, h):
    return client.get("/api/v1/health", headers=h).json()


def test_failed_refresh_is_reported_and_resolved_by_the_next_good_upload(client):
    h, _ = register(client, "Fail", "f@mon.test")
    sid = _source(client, h)
    bad = _upload(client, h, sid, "bad.csv", b"a,A\n1,2\n")          # duplicate column names: a permanent data error
    assert bad["status"] == "failed"
    hl = _health(client, h)
    assert hl["sources"][0]["state"] == "failed" and "bad.csv" in hl["sources"][0]["message"]
    assert [i["kind"] for i in hl["issues"]] == ["refresh_failed"] and hl["issues"][0]["severity"] == "high"
    assert _upload(client, h, sid, "good.csv", GOOD)["status"] == "succeeded"
    hl = _health(client, h)
    assert hl["sources"][0]["state"] == "ok" and hl["issues"] == []   # the latest attempt worked: resolved


def test_schema_change_is_reported_with_what_changed(client):
    h, _ = register(client, "Schema", "s@mon.test")
    sid = _source(client, h)
    _upload(client, h, sid, "orders.csv", GOOD)
    _upload(client, h, sid, "orders.csv", b"order_id,order_date,net_sales\nC,2026-02-01,50\n")   # 'cost' column removed
    issues = [i for i in _health(client, h)["issues"] if i["kind"] == "schema_changed"]
    assert len(issues) == 1 and issues[0]["severity"] == "medium"
    assert "columns removed: cost" in issues[0]["message"] and "mapping" in issues[0]["message"]


def test_added_columns_alone_are_low_severity(client):
    h, _ = register(client, "Added", "a@mon.test")
    sid = _source(client, h)
    _upload(client, h, sid, "orders.csv", GOOD)
    _upload(client, h, sid, "orders.csv", GOOD.replace(b"cost", b"cost,channel").replace(b",60", b",60,web").replace(b",130", b",130,web"))
    issues = [i for i in _health(client, h)["issues"] if i["kind"] == "schema_changed"]
    assert issues and issues[0]["severity"] == "low"


def test_missing_file_is_reported_only_when_a_cadence_is_set(client):
    h, tid = register(client, "Late", "l@mon.test")
    sid = _source(client, h)
    _upload(client, h, sid, "orders.csv", GOOD)
    later = datetime.now(timezone.utc) + timedelta(days=3)
    with SessionLocal() as db:
        assert health.issues(db, tid, later) == []                    # no expectation: never "late"
    assert client.patch(f"/api/v1/data-sources/{sid}", json={"expected_every_days": 1}, headers=h).status_code == 200
    with SessionLocal() as db:
        assert health.issues(db, tid, datetime.now(timezone.utc)) == []          # fresh
        i = health.issues(db, tid, later)
        assert [x["kind"] for x in i] == ["file_missing"] and "No new file for 3 days" in i[0]["message"]
    assert client.get("/api/v1/data-sources", headers=h).json()[0]["expected_every_days"] == 1
    assert client.patch(f"/api/v1/data-sources/{sid}", json={"expected_every_days": 0}, headers=h).json()["expected_every_days"] is None


def test_source_cadence_rules_roles_and_tenant_scope(client):
    h1, _ = register(client, "One", "1@mon.test")
    h2, _ = register(client, "Two", "2@mon.test")
    sid = _source(client, h1)
    assert client.patch(f"/api/v1/data-sources/{sid}", json={"expected_every_days": 400}, headers=h1).status_code == 422
    assert client.patch(f"/api/v1/data-sources/{sid}", json={"expected_every_days": -1}, headers=h1).status_code == 422
    assert client.patch(f"/api/v1/data-sources/{sid}", json={"expected_every_days": 7}, headers=h2).status_code == 404
    assert _health(client, h2)["sources"] == []
    assert client.get("/api/v1/health").status_code == 401


def test_worker_records_each_issue_once_in_the_activity_log(client):
    h, _ = register(client, "Once", "o@mon.test")
    sid = _source(client, h)
    _upload(client, h, sid, "bad.csv", b"a,A\n1,2\n")
    with SessionLocal() as db:
        first, second = health.record_new_issues(db), health.record_new_issues(db)
    assert first == 1 and second == 0
    acts = [a for a in client.get("/api/v1/activity", headers=h).json() if a["action"] == "health.issue"]
    assert len(acts) == 1 and acts[0]["detail"]["kind"] == "refresh_failed"


# ----------------------------------------------------------------------------------------------- summaries
MONTHS = (b"order_id,order_date,net_sales,cost\nA,2026-01-10,100,60\nB,2026-02-10,100,60\n"
          b"C,2026-03-10,150,90\nD,2026-04-05,10,5\n")


def _approved(client, h, data=MONTHS):
    sid = _source(client, h)
    job = _upload(client, h, sid, "orders.csv", data)
    assert job["datasets"][0]["model_status"] == "approved"          # tests enable automatic modelling below
    return sid


def _auto(client, h):
    assert client.patch("/api/v1/settings", json={"auto_model": True}, headers=h).status_code == 200


def test_nothing_to_summarise_before_any_data_is_modelled(client):
    h, _ = register(client, "Empty", "e@mon.test")
    r = client.post("/api/v1/summaries/generate", headers=h)
    assert r.status_code == 422 and "upload data" in r.json()["detail"].lower()
    assert client.get("/api/v1/summaries", headers=h).json() == []


def test_summary_uses_real_month_on_month_numbers_and_real_events(client):
    h, _ = register(client, "Sum", "s@mon.test")
    _auto(client, h)
    _approved(client, h)
    q = client.post("/api/v1/analytics/query", json={"question": "total revenue"}, headers=h).json()
    assert q["outcome"] == "answered"
    r = client.post("/api/v1/summaries/generate", headers=h)
    assert r.status_code == 201, r.text
    c = r.json()["content"]
    nr = next(x for x in c["metrics"] if x["label"] == "Net Revenue")
    assert nr["total"]["current"] == 150 and nr["total"]["previous"] == 100 and nr["total"]["percent"] == 50.0
    assert "Net Revenue up 50.0%" in c["headline"]
    assert c["operations"]["files_uploaded"] == 1 and c["operations"]["questions_answered"] >= 1
    assert c["operations"]["rows_loaded"] == 4 and "does not explain why" in c["note"]
    assert client.get(f"/api/v1/summaries/{r.json()['id']}", headers=h).json()["content"]["headline"] == c["headline"]
    again = client.post("/api/v1/summaries/generate", headers=h)           # same period: replaced, not duplicated
    assert again.status_code == 201 and len(client.get("/api/v1/summaries", headers=h).json()) == 1


def test_attention_items_appear_in_the_summary(client):
    h, tid = register(client, "Attn", "a@mon2.test")
    _auto(client, h)
    sid = _approved(client, h)
    _upload(client, h, sid, "orders.csv", b"a,A\n1,2\n")                    # latest attempt fails
    c = client.post("/api/v1/summaries/generate", headers=h).json()["content"]
    assert c["attention"] and c["attention"][0]["kind"] == "refresh_failed"
    assert "need attention" in c["headline"] or "needs attention" in c["headline"].lower()


def test_weekly_run_creates_last_weeks_summary_once(client):
    h, tid = register(client, "Weekly", "w@mon.test")
    _auto(client, h)
    _approved(client, h)
    next_week = datetime.now(timezone.utc) + timedelta(days=7)               # so that "last full week" holds today's upload
    with SessionLocal() as db:
        assert summaries.run_weekly(db, next_week) == 1
        assert summaries.run_weekly(db, next_week) == 0                      # already there
    rows = client.get("/api/v1/summaries", headers=h).json()
    assert len(rows) == 1 and rows[0]["kind"] == "weekly"
    start = date.fromisoformat(rows[0]["period_start"])
    assert start.weekday() == 0 and (date.fromisoformat(rows[0]["period_end"]) - start).days == 6
    full = client.get(f"/api/v1/summaries/{rows[0]['id']}", headers=h).json()
    assert full["content"]["operations"]["files_uploaded"] == 1


def test_summaries_are_tenant_scoped(client):
    h1, _ = register(client, "A", "a@mon3.test")
    h2, _ = register(client, "B", "b@mon3.test")
    _auto(client, h1)
    _approved(client, h1)
    sid = client.post("/api/v1/summaries/generate", headers=h1).json()["id"]
    assert client.get(f"/api/v1/summaries/{sid}", headers=h2).status_code == 404
    assert client.get("/api/v1/summaries", headers=h2).json() == []
    assert client.get("/api/v1/summaries").status_code == 401


def test_viewers_can_read_but_not_generate(client):
    h, _ = register(client, "Roles", "r@mon.test")
    assert client.post("/api/v1/users", headers=h, json={"email": "v@mon.test", "role": "viewer", "password": "viewer-pass-12"}).status_code == 201
    vh = {"Authorization": "Bearer " + client.post("/api/v1/auth/login", json={"email": "v@mon.test", "password": "viewer-pass-12"}).json()["token"]}
    assert client.get("/api/v1/health", headers=vh).status_code == 200
    assert client.get("/api/v1/summaries", headers=vh).status_code == 200
    assert client.post("/api/v1/summaries/generate", headers=vh).status_code == 403
