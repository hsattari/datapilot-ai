"""First automatic build creates starter content once: Overview dashboard, saved reports, a management summary."""
from .conftest import register
from .test_insights import csv, steady


def auto_tenant(client, email="s@st.test", org="St"):
    h, tid = register(client, org, email)
    assert client.patch("/api/v1/settings", headers=h, json={"auto_model": True}).status_code == 200, "settings route"
    return h, tid


def upload(client, h, rows, name="sales.csv"):
    sid = client.post("/api/v1/data-sources", json={"name": name}, headers=h).json()["id"]
    r = client.post(f"/api/v1/data-sources/{sid}/uploads", headers=h, files={"file": (name, csv(rows), "text/csv")})
    assert r.status_code in (200, 201, 202), r.text
    from app.db import SessionLocal
    from app.services.ingestion import run_pending
    with SessionLocal() as db:
        run_pending(db)


def test_first_upload_creates_dashboard_reports_and_summary(client):
    h, _ = auto_tenant(client)
    assert client.get("/api/v1/dashboards", headers=h).json() == []
    upload(client, h, steady())
    dash = client.get("/api/v1/dashboards", headers=h).json()
    assert [d["title"] for d in dash] == ["Overview"]
    view = client.get(f"/api/v1/dashboards/{dash[0]['id']}", headers=h).json()
    assert any(t["status"] == "ok" for t in view["rendered"])
    titles = [r["title"] for r in client.get("/api/v1/reports", headers=h).json()]
    assert "Net revenue by month" in titles and "Why did net revenue change?" in titles
    assert client.get("/api/v1/summaries", headers=h).json()


def test_saved_reports_actually_run(client):
    h, _ = auto_tenant(client, "r@st.test", "St2")
    upload(client, h, steady())
    for r in client.get("/api/v1/reports", headers=h).json():
        run = client.get(f"/api/v1/reports/{r['id']}/run", headers=h)
        assert run.status_code == 200, (r["title"], run.text)


def test_created_once_even_if_deleted_and_more_data_arrives(client):
    h, _ = auto_tenant(client, "o@st.test", "St3")
    upload(client, h, steady())
    for d in client.get("/api/v1/dashboards", headers=h).json():
        assert client.delete(f"/api/v1/dashboards/{d['id']}", headers=h).status_code in (200, 204)
    n = len(client.get("/api/v1/reports", headers=h).json())
    upload(client, h, steady(), name="more.csv")
    assert client.get("/api/v1/dashboards", headers=h).json() == []
    assert len(client.get("/api/v1/reports", headers=h).json()) == n


def test_nothing_created_when_auto_model_is_off_or_other_tenant(client):
    h, _ = register(client, "St4", "n@st.test")      # tests default to auto_model off
    upload(client, h, steady())
    assert client.get("/api/v1/dashboards", headers=h).json() == []
    h2, _ = auto_tenant(client, "p@st.test", "St5")
    upload(client, h2, steady())
    assert client.get("/api/v1/dashboards", headers=h).json() == []     # still empty for the first organisation
