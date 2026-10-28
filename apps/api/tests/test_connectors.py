"""URL connector and SSRF protection. No real network: names are resolved by a stub and HTTP is a mock transport."""
import httpx
import pytest

from app.db import SessionLocal
from app.services import connectors, netguard
from app.services.ingestion import run_pending

from .conftest import register

CSV = b"order_id,order_date,net_sales,cost\nA,2026-01-10,100,60\nB,2026-01-20,200,130\n"
PUBLIC = "93.184.216.34"


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    table = {"files.example.com": [PUBLIC], "evil.example.com": ["10.0.0.5"], "mixed.example.com": [PUBLIC, "169.254.169.254"],
             "v6.example.com": ["::ffff:127.0.0.1"], "other.example.com": ["93.184.216.35"]}
    def fake(host, port):
        if host not in table:
            raise netguard.UrlRejected("The address could not be found.")
        return table[host]
    monkeypatch.setattr(netguard, "resolve", fake)


def mock(handler):
    return httpx.MockTransport(handler)


# ---- the guard itself -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://files.example.com/a.csv", "ftp://files.example.com/a.csv", "file:///etc/passwd", "https://user:pw@files.example.com/a.csv",
    "https://files.example.com:8443/a.csv", "https://localhost/a.csv", "https://printer.local/a.csv", "https://db.internal/a.csv",
    "https://intranet/a.csv", "https:///a.csv", "", "https://files.example.com/a b.csv", "https://x@y@files.example.com/"])
def test_bad_addresses_are_refused_before_any_network(url):
    with pytest.raises(netguard.UrlRejected):
        netguard.check_url(url)


@pytest.mark.parametrize("host", ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
                                  "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:10.0.0.1", "224.0.0.1",
                                  "evil.example.com", "mixed.example.com", "v6.example.com"])
def test_private_and_metadata_addresses_are_refused_including_by_name(host):
    with pytest.raises(netguard.UrlRejected):
        netguard.check_host(host, 443)


def test_public_address_is_accepted():
    assert netguard.check_host("files.example.com", 443) == PUBLIC


def test_connection_goes_to_the_checked_address_with_the_real_host_name():
    seen = {}
    def h(req):
        seen.update(ip=req.url.host, host=req.headers["host"]); return httpx.Response(200, content=CSV, headers={"content-type": "text/csv"})
    got = netguard.fetch("https://files.example.com/a.csv", max_bytes=1000, transport=mock(h))
    assert got.data == CSV and seen == {"ip": PUBLIC, "host": "files.example.com"}


def test_redirect_to_a_private_address_is_refused_on_the_hop():
    calls = []
    def h(req):
        calls.append(str(req.url))
        return httpx.Response(302, headers={"location": "https://evil.example.com/secret.csv"})
    with pytest.raises(netguard.UrlRejected, match="private or local"):
        netguard.fetch("https://files.example.com/a.csv", max_bytes=1000, transport=mock(h))
    assert len(calls) == 1                       # the second hop never left the process


def test_redirect_to_metadata_ip_literal_and_to_http_are_refused():
    for loc in ("https://169.254.169.254/latest/meta-data/", "http://files.example.com/a.csv", "https://localhost/a.csv"):
        with pytest.raises(netguard.UrlRejected):
            netguard.fetch("https://files.example.com/a.csv", max_bytes=1000,
                           transport=mock(lambda r, loc=loc: httpx.Response(301, headers={"location": loc})))


def test_a_safe_redirect_is_followed_and_a_loop_is_stopped():
    def ok(req):
        if req.headers["host"] == "files.example.com":
            return httpx.Response(302, headers={"location": "https://other.example.com/b.csv"})
        return httpx.Response(200, content=CSV, headers={"content-type": "text/csv"})
    got = netguard.fetch("https://files.example.com/a.csv", max_bytes=1000, transport=mock(ok))
    assert got.final_url == "https://other.example.com/b.csv"
    with pytest.raises(netguard.UrlRejected, match="Too many redirects"):
        netguard.fetch("https://files.example.com/a.csv", max_bytes=1000,
                       transport=mock(lambda r: httpx.Response(302, headers={"location": "https://files.example.com/a.csv"})))


def test_size_cap_applies_to_declared_and_actual_size_and_status_must_be_200():
    with pytest.raises(netguard.UrlRejected, match="larger"):
        netguard.fetch("https://files.example.com/a.csv", max_bytes=10, transport=mock(lambda r: httpx.Response(200, content=b"x" * 100)))
    with pytest.raises(netguard.UrlRejected, match="larger"):
        netguard.fetch("https://files.example.com/a.csv", max_bytes=10,
                       transport=mock(lambda r: httpx.Response(200, headers={"content-length": "999"}, content=b"x")))
    with pytest.raises(netguard.UrlRejected, match="404"):
        netguard.fetch("https://files.example.com/a.csv", max_bytes=10, transport=mock(lambda r: httpx.Response(404)))


def test_environment_proxy_is_not_used(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    got = netguard.fetch("https://files.example.com/a.csv", max_bytes=1000,
                         transport=mock(lambda r: httpx.Response(200, content=CSV)))
    assert got.data == CSV


# ---- through the API ----------------------------------------------------------------------------------------------------

@pytest.fixture
def serve(monkeypatch):
    state = {"body": CSV, "status": 200, "ctype": "text/csv"}
    def handler(req):
        return httpx.Response(state["status"], content=state["body"], headers={"content-type": state["ctype"]})
    real = netguard.fetch
    monkeypatch.setattr(netguard, "fetch", lambda url, **kw: real(url, **{**kw, "transport": mock(handler)}))
    return state


def setup_source(client, email="c@con.test", org="Con"):
    h, _ = register(client, org, email)
    sid = client.post("/api/v1/data-sources", json={"name": "Shop feed"}, headers=h).json()["id"]
    return h, sid


def run_jobs():
    with SessionLocal() as db:
        run_pending(db)


def test_unsafe_addresses_cannot_be_connected(client):
    h, sid = setup_source(client)
    for url in ("https://evil.example.com/a.csv", "https://169.254.169.254/latest/meta-data/", "http://files.example.com/a.csv",
                "https://localhost/a.csv", "https://mixed.example.com/a.csv"):
        r = client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": url}, headers=h)
        assert r.status_code == 422, url
    assert client.get(f"/api/v1/data-sources/{sid}/connection", headers=h).json()["url"] is None
    ev = client.get("/api/v1/audit", headers=h)
    if ev.status_code == 200:
        assert "source.connection_rejected" in ev.text


def test_fetch_now_ingests_like_an_upload_and_an_unchanged_file_is_skipped(client, serve):
    h, sid = setup_source(client)
    assert client.post(f"/api/v1/data-sources/{sid}/sync", headers=h).status_code == 409      # not connected yet
    assert client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": "https://files.example.com/sales.csv?token=SECRET"},
                      headers=h).status_code == 200
    r = client.post(f"/api/v1/data-sources/{sid}/sync", headers=h)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    run_jobs()
    assert client.get(f"/api/v1/jobs/{r.json()['job_id']}", headers=h).json()["status"] == "succeeded"
    again = client.post(f"/api/v1/data-sources/{sid}/sync", headers=h).json()
    assert again["status"] == "unchanged"
    serve["body"] = CSV + b"C,2026-01-25,50,20\n"
    third = client.post(f"/api/v1/data-sources/{sid}/sync", headers=h).json()
    assert third["status"] == "queued" and third["version"] == 2           # same name: a new version, accumulated by order id
    src = next(s for s in client.get("/api/v1/data-sources", headers=h).json() if s["id"] == sid)
    assert src["files"] == 2


def test_audit_never_holds_the_query_string(client, serve):
    h, sid = setup_source(client)
    client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": "https://files.example.com/sales.csv?token=SECRET"}, headers=h)
    client.post(f"/api/v1/data-sources/{sid}/sync", headers=h)
    from app import models as m
    with SessionLocal() as db:
        text = " ".join(str(e.detail) for e in db.query(m.AuditEvent).all())
    assert "SECRET" not in text and "files.example.com/sales.csv" in text


def test_failures_are_reported_in_health_and_cleared_by_the_next_success(client, serve):
    h, sid = setup_source(client)
    client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": "https://files.example.com/sales.csv"}, headers=h)
    serve["status"] = 500
    assert client.post(f"/api/v1/data-sources/{sid}/sync", headers=h).status_code == 422
    hl = client.get("/api/v1/health", headers=h).json()
    assert hl["sources"][0]["state"] == "failed" and "500" in hl["sources"][0]["message"]
    assert hl["issues"][0]["kind"] == "refresh_failed"
    serve["status"] = 200
    assert client.post(f"/api/v1/data-sources/{sid}/sync", headers=h).status_code == 202
    run_jobs()
    assert client.get("/api/v1/health", headers=h).json()["sources"][0]["state"] == "ok"


def test_wrong_content_is_rejected_by_the_normal_upload_checks(client, serve):
    h, sid = setup_source(client)
    client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": "https://files.example.com/export"}, headers=h)
    serve.update(body=b"<html>login</html>", ctype="text/html")
    r = client.post(f"/api/v1/data-sources/{sid}/sync", headers=h)
    assert r.status_code == 422 and "extension" in r.json()["detail"]
    client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": "https://files.example.com/export.xlsx"}, headers=h)
    serve.update(body=b"not a zip", ctype="application/octet-stream")
    assert client.post(f"/api/v1/data-sources/{sid}/sync", headers=h).status_code == 422


def test_content_type_names_the_file_when_the_address_has_no_extension(client, serve):
    h, sid = setup_source(client)
    client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": "https://files.example.com/export"}, headers=h)
    assert client.post(f"/api/v1/data-sources/{sid}/sync", headers=h).status_code == 202


def test_other_organisations_cannot_see_or_sync_the_source(client, serve):
    h, sid = setup_source(client)
    client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": "https://files.example.com/sales.csv"}, headers=h)
    h2, _ = register(client, "Other", "o@con.test")
    assert client.post(f"/api/v1/data-sources/{sid}/sync", headers=h2).status_code == 404
    assert client.get(f"/api/v1/data-sources/{sid}/connection", headers=h2).status_code == 404
    assert client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": "https://files.example.com/x.csv"}, headers=h2).status_code == 404


def test_viewers_cannot_connect_or_sync(client, serve):
    from .test_workspace import add_viewer
    h, tid = register(client, "ViewCo", "vc@con.test")
    sid = client.post("/api/v1/data-sources", json={"name": "Feed"}, headers=h).json()["id"]
    client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": "https://files.example.com/a.csv"}, headers=h)
    v = add_viewer(tid, "v@con.test", "viewer")
    assert client.post(f"/api/v1/data-sources/{sid}/sync", headers=v).status_code == 403
    assert client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": "https://files.example.com/x.csv"}, headers=v).status_code == 403
    assert client.get(f"/api/v1/data-sources/{sid}/connection", headers=v).status_code == 403


def test_scheduled_sync_runs_only_when_due_and_one_failure_does_not_stop_others(client, serve):
    h, sid = setup_source(client)
    h2, sid2 = setup_source(client, email="d@con.test", org="Con2")
    client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": "https://files.example.com/a.csv", "sync_every_hours": 6}, headers=h)
    client.put(f"/api/v1/data-sources/{sid2}/connection", json={"url": "https://files.example.com/b.csv", "sync_every_hours": 6}, headers=h2)
    from app import models as m
    with SessionLocal() as db:
        assert connectors.run_due(db) == 2                     # never synced: both due
        assert connectors.run_due(db) == 0                     # just synced: neither due
        s = db.get(m.DataSource, sid)
        s.last_sync_at = connectors._now() - connectors.timedelta(hours=7)
        db.commit()
        assert connectors.run_due(db) == 1
    run_jobs()
    assert all(x["state"] == "ok" for x in client.get("/api/v1/health", headers=h).json()["sources"])


def test_disconnecting_removes_the_address(client, serve):
    h, sid = setup_source(client)
    client.put(f"/api/v1/data-sources/{sid}/connection", json={"url": "https://files.example.com/a.csv", "sync_every_hours": 6}, headers=h)
    assert client.delete(f"/api/v1/data-sources/{sid}/connection", headers=h).status_code == 204
    assert client.get(f"/api/v1/data-sources/{sid}/connection", headers=h).json()["url"] is None
    assert client.post(f"/api/v1/data-sources/{sid}/sync", headers=h).status_code == 409
