"""Dashboards: tiles are recomputed through the governed engine each time; nothing unapproved, foreign or broken
can leak into or break a dashboard."""
from .conftest import register
from .test_end_to_end import setup_tenant
from .test_monitoring import MONTHS, _approved, _auto


def _tile(kind, **kw):
    return {"kind": kind, **kw}


def _approve_gp(client, h):
    assert client.post("/api/v1/metrics/gross_profit/approve", headers=h).status_code == 200


def test_overview_has_one_tile_per_approved_metric_with_real_numbers(client):
    h, *_ = setup_tenant(client)
    _approve_gp(client, h)
    d = client.post("/api/v1/dashboards/auto", headers=h)
    assert d.status_code == 201, d.text
    full = client.get(f"/api/v1/dashboards/{d.json()['id']}", headers=h).json()
    by = {t["title"]: t for t in full["rendered"]}
    assert float(by["Net Revenue"]["total"]) == 350 and float(by["Gross Profit"]["total"]) == 140 and float(by["Orders"]["total"]) == 3
    assert all(t["status"] == "ok" and t["trend"] for t in full["rendered"])
    assert by["Gross Profit"]["metric"]["expression"] == "net_sales - cost_of_goods_sold"


def test_auto_is_idempotent_and_refreshes_tiles(client):
    h, *_ = setup_tenant(client)
    first = client.post("/api/v1/dashboards/auto", headers=h).json()
    assert len(first["tiles"]) == 2                                    # gross profit is not approved yet
    _approve_gp(client, h)
    second = client.post("/api/v1/dashboards/auto", headers=h).json()
    assert second["id"] == first["id"] and len(second["tiles"]) == 3
    assert len(client.get("/api/v1/dashboards", headers=h).json()) == 1


def test_unapproved_metric_tile_is_refused_then_works_once_approved(client):
    h, *_ = setup_tenant(client)
    d = client.post("/api/v1/dashboards", headers=h, json={"title": "Profit", "tiles": [_tile("metric", metric="gross_profit")]})
    assert d.status_code == 201
    t = client.get(f"/api/v1/dashboards/{d.json()['id']}", headers=h).json()["rendered"][0]
    assert t["status"] == "unavailable" and "not approved" in t["message"].lower()    # approval re-checked on every view
    _approve_gp(client, h)
    t = client.get(f"/api/v1/dashboards/{d.json()['id']}", headers=h).json()["rendered"][0]
    assert t["status"] == "ok" and float(t["total"]) == 140


def test_report_tiles_run_the_saved_plan(client):
    h, *_ = setup_tenant(client)
    rid = client.post("/api/v1/reports", headers=h, json={"title": "By product", "question": "net revenue by product"}).json()["id"]
    d = client.post("/api/v1/dashboards", headers=h, json={"title": "Sales", "tiles": [_tile("report", report_id=rid)]}).json()
    t = client.get(f"/api/v1/dashboards/{d['id']}", headers=h).json()["rendered"][0]
    assert t["status"] == "ok" and {r[0]: float(r[1]) for r in t["rows"]} == {"Widget": 150, "Gadget": 200}
    client.delete(f"/api/v1/reports/{rid}", headers=h)                  # a deleted report leaves a clear tile, not an error
    t = client.get(f"/api/v1/dashboards/{d['id']}", headers=h).json()["rendered"][0]
    assert t["status"] == "unavailable" and "no longer exists" in t["message"]


def test_why_did_it_change_report_shows_its_headline_on_a_dashboard(client):
    h, _ = register(client, "Why", "w@dash.test")
    _auto(client, h)
    _approved(client, h)
    rid = client.post("/api/v1/reports", headers=h, json={"title": "Why", "question": "Why did net revenue change?"}).json()["id"]
    d = client.post("/api/v1/dashboards", headers=h, json={"title": "Why", "tiles": [_tile("report", report_id=rid)]}).json()
    t = client.get(f"/api/v1/dashboards/{d['id']}", headers=h).json()["rendered"][0]
    assert t["visualization"] == "text" and "Net Revenue rose" in t["headline"]


def test_a_tile_with_no_data_model_is_unavailable_not_a_crash(client):
    h, _ = register(client, "Bare", "b@dash.test")
    d = client.post("/api/v1/dashboards/auto", headers=h)             # built-in metrics are approved, but there is no data
    full = client.get(f"/api/v1/dashboards/{d.json()['id']}", headers=h)
    assert full.status_code == 200 and all(t["status"] == "unavailable" for t in full.json()["rendered"])


def test_validation_and_limits(client):
    h, *_ = setup_tenant(client)
    post = lambda body: client.post("/api/v1/dashboards", headers=h, json=body)  # noqa: E731
    assert post({"title": "x", "tiles": []}).status_code == 422
    assert post({"title": "", "tiles": [_tile("metric", metric="net_revenue")]}).status_code == 422
    assert post({"title": "x", "tiles": [_tile("metric", metric="nope")]}).status_code == 422
    assert post({"title": "x", "tiles": [_tile("metric")]}).status_code == 422
    assert post({"title": "x", "tiles": [_tile("report")]}).status_code == 422
    assert post({"title": "x", "tiles": [_tile("metric", metric="net_revenue")] * 13}).status_code == 422
    assert post({"title": "x", "tiles": [{"kind": "script", "metric": "net_revenue"}]}).status_code == 422
    for i in range(20):
        assert post({"title": f"d{i}", "tiles": [_tile("metric", metric="net_revenue")]}).status_code == 201
    assert post({"title": "one too many", "tiles": [_tile("metric", metric="net_revenue")]}).status_code == 429


def test_dashboards_are_tenant_scoped(client):
    h1, *_ = setup_tenant(client, org="One", email="1@dash.test")
    h2, _ = register(client, "Two", "2@dash.test")
    rid = client.post("/api/v1/reports", headers=h1, json={"title": "R", "question": "net revenue by product"}).json()["id"]
    d = client.post("/api/v1/dashboards", headers=h1, json={"title": "Mine", "tiles": [_tile("report", report_id=rid)]}).json()
    assert client.get(f"/api/v1/dashboards/{d['id']}", headers=h2).status_code == 404
    assert client.delete(f"/api/v1/dashboards/{d['id']}", headers=h2).status_code == 404
    assert client.get("/api/v1/dashboards", headers=h2).json() == []
    # another organisation's report cannot be put on a dashboard
    r = client.post("/api/v1/dashboards", headers=h2, json={"title": "Steal", "tiles": [_tile("report", report_id=rid)]})
    assert r.status_code == 404
    assert client.get("/api/v1/dashboards").status_code == 401


def test_viewers_can_look_but_not_change(client):
    h, *_ = setup_tenant(client, org="Roles", email="r@dash.test")
    d = client.post("/api/v1/dashboards/auto", headers=h).json()
    assert client.post("/api/v1/users", headers=h, json={"email": "v@dash.test", "role": "viewer", "password": "viewer-pass-12"}).status_code == 201
    tok = client.post("/api/v1/auth/login", json={"email": "v@dash.test", "password": "viewer-pass-12"}).json()["token"]
    vh = {"Authorization": f"Bearer {tok}"}
    assert client.get(f"/api/v1/dashboards/{d['id']}", headers=vh).status_code == 200
    assert client.get("/api/v1/dashboards", headers=vh).status_code == 200
    assert client.post("/api/v1/dashboards/auto", headers=vh).status_code == 403
    assert client.delete(f"/api/v1/dashboards/{d['id']}", headers=vh).status_code == 403


def test_deleting_a_dashboard_is_logged(client):
    h, *_ = setup_tenant(client, org="Del", email="d@dash.test")
    d = client.post("/api/v1/dashboards/auto", headers=h).json()
    assert client.delete(f"/api/v1/dashboards/{d['id']}", headers=h).status_code == 204
    acts = [a["action"] for a in client.get("/api/v1/activity", headers=h).json()]
    assert "dashboard.auto_created" in acts and "dashboard.deleted" in acts
