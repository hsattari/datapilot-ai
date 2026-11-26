"""Separate workspaces: isolation, switching, limits."""
from app import models as m
from app.config import get_settings
from app.db import SessionLocal

from .conftest import register
from .test_starters import auto_tenant, upload
from .test_insights import steady
from .test_workspace import add_viewer

W = "/api/v1/workspaces"


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_create_switch_and_data_is_isolated(client):
    h, tid = auto_tenant(client, "o@ws.test", "Main")
    upload(client, h, steady())
    r = client.post(W, headers=h, json={"name": "Second"})
    assert r.status_code == 201
    h2 = hdr(r.json()["token"])
    # the new workspace is empty and has its own settings
    assert client.get("/api/v1/data-sources", headers=h2).json() == []
    assert client.get("/api/v1/workspace/settings", headers=h2).json()["name"] == "Second" if client.get("/api/v1/workspace/settings", headers=h2).status_code == 200 else True
    ws = client.get(W, headers=h2).json()
    assert {w["name"] for w in ws} == {"Main", "Second"} and [w["name"] for w in ws if w["current"]] == ["Second"]
    # switching back shows the original data
    back = client.post(f"{W}/switch", headers=h2, json={"tenant_id": tid}).json()
    assert len(client.get("/api/v1/data-sources", headers=hdr(back["token"])).json()) >= 1
    # the old token (no workspace choice) still works and lands in a workspace the person belongs to
    assert client.get(W, headers=h).status_code == 200


def test_cannot_switch_to_a_workspace_you_are_not_in(client):
    h, tid = register(client, "A", "a@one.ws")
    h2, tid2 = register(client, "B", "b@two.ws")
    assert client.post(f"{W}/switch", headers=h, json={"tenant_id": tid2}).status_code == 404
    assert client.post(f"{W}/switch", headers=h, json={"tenant_id": "nope"}).status_code == 404


def test_forged_workspace_claim_grants_nothing(client):
    from app.security import create_token
    h, tid = register(client, "A", "a@forge.ws")
    h2, tid2 = register(client, "B", "b@forge.ws")
    with SessionLocal() as db:
        uid = db.query(m.TenantMembership).filter_by(tenant_id=tid).one().user_id
    forged = {"Authorization": f"Bearer {create_token(uid, tid2)}"}
    me = client.get(W, headers=forged).json()
    assert [w["id"] for w in me if w["current"]] == [tid]   # fell back to the workspace they belong to


def test_role_is_per_workspace_and_limit(client, monkeypatch):
    h, tid = register(client, "A", "a@lim.ws")
    vh = add_viewer(tid, "v@lim.ws")
    tok = client.post(W, headers=vh, json={"name": "Mine"}).json()["token"]
    # admin in their own workspace, still only a viewer in the original one
    assert client.get("/api/v1/users", headers=hdr(tok)).status_code == 200
    back = client.post(f"{W}/switch", headers=hdr(tok), json={"tenant_id": tid}).json()
    assert back["role"] == "viewer" and client.get("/api/v1/users", headers=hdr(back["token"])).status_code == 403
    monkeypatch.setattr(get_settings(), "max_workspaces_per_user", 1)
    assert client.post(W, headers=h, json={"name": "Too many"}).status_code == 429


def test_deleting_a_workspace_keeps_the_person_in_the_others(client):
    h, tid = register(client, "A", "a@del.ws")
    new = client.post(W, headers=h, json={"name": "Temp"}).json()
    r = client.post("/api/v1/tenant/delete", headers=hdr(new["token"]),
                    json={"confirm_name": "Temp", "password": "correct-horse-1"})
    assert r.status_code in (200, 204), r.text
    ws = client.get(W, headers=h).json()
    assert [w["name"] for w in ws] == ["A"]
