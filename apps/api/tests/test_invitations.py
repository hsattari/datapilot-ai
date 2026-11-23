"""Invitations by e-mail: one-time link, expiry, revoke, roles, no mail server fallback."""
from datetime import datetime, timedelta, timezone

from app import models as m
from app.db import SessionLocal
from app.services import mailer

from .conftest import register
from .test_workspace import add_viewer

URL = "/api/v1/invitations"


def token_of(link: str) -> str:
    return link.split("invite=")[1]


def test_invite_accept_once_and_sign_in(client):
    h, _ = register(client, "Inv", "a@inv.test")
    r = client.post(URL, headers=h, json={"email": "New@Inv.test", "role": "analyst"})
    assert r.status_code == 201 and r.json()["status"] == "pending" and r.json()["email_sent"] is False
    tok = token_of(r.json()["link"])
    pv = client.get("/api/v1/auth/invitation", params={"token": tok}).json()
    assert pv == {"email": "new@inv.test", "role": "analyst", "organisation": "Inv"}
    a = client.post("/api/v1/auth/accept-invitation", json={"token": tok, "password": "a-long-password-1"})
    assert a.status_code == 200
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {a.json()['token']}"}).json()
    assert me["email"] == "new@inv.test" and me["role"] == "analyst"
    assert client.post("/api/v1/auth/login", json={"email": "new@inv.test", "password": "a-long-password-1"}).status_code == 200
    # works once
    assert client.post("/api/v1/auth/accept-invitation", json={"token": tok, "password": "another-long-pass"}).status_code == 410
    assert client.get(URL, headers=h).json()[0]["status"] == "accepted"


def test_token_is_stored_hashed_only(client):
    h, _ = register(client, "Hash", "a@hash.test")
    tok = token_of(client.post(URL, headers=h, json={"email": "x@hash.test", "role": "viewer"}).json()["link"])
    with SessionLocal() as db:
        row = db.query(m.Invitation).one()
        assert tok not in row.token_hash and len(row.token_hash) == 64


def test_expired_revoked_and_replaced_links_fail(client):
    h, _ = register(client, "Exp", "a@exp.test")
    first = client.post(URL, headers=h, json={"email": "p@exp.test", "role": "viewer"}).json()
    second = client.post(URL, headers=h, json={"email": "p@exp.test", "role": "viewer"}).json()
    assert client.get("/api/v1/auth/invitation", params={"token": token_of(first["link"])}).status_code == 410  # replaced
    assert client.delete(f"{URL}/{second['id']}", headers=h).status_code == 204
    assert client.get("/api/v1/auth/invitation", params={"token": token_of(second["link"])}).status_code == 410  # revoked
    third = client.post(URL, headers=h, json={"email": "p@exp.test", "role": "viewer"}).json()
    with SessionLocal() as db:
        i = db.get(m.Invitation, third["id"])
        i.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert client.get("/api/v1/auth/invitation", params={"token": token_of(third["link"])}).status_code == 410


def test_only_admins_invite_and_existing_accounts_conflict(client):
    h, tid = register(client, "Perm", "a@perm.test")
    vh = add_viewer(tid, "v@perm.test")
    assert client.post(URL, headers=vh, json={"email": "z@perm.test", "role": "viewer"}).status_code == 403
    assert client.get(URL, headers=vh).status_code == 403
    assert client.post(URL, headers=h, json={"email": "v@perm.test", "role": "viewer"}).status_code == 409
    assert client.post(URL, headers=h, json={"email": "not-an-email", "role": "viewer"}).status_code == 422


def test_other_organisations_cannot_revoke(client):
    h, _ = register(client, "One", "a@one.test")
    h2, _ = register(client, "Two", "b@two.test")
    inv = client.post(URL, headers=h, json={"email": "q@one.test", "role": "viewer"}).json()
    assert client.delete(f"{URL}/{inv['id']}", headers=h2).status_code == 404
    assert client.get(URL, headers=h2).json() == []


def test_with_mail_server_the_link_is_emailed_not_returned(client, monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, body: sent.append((to, subject, body)) or True)
    h, _ = register(client, "Mail", "a@mail.test")
    r = client.post(URL, headers=h, json={"email": "m@mail.test", "role": "viewer"}).json()
    assert r["email_sent"] is True and "link" not in r
    assert sent[0][0] == "m@mail.test" and "invite=" in sent[0][2]
    events = client.get("/api/v1/audit", headers=h).json() if client.get("/api/v1/audit", headers=h).status_code == 200 else []
    assert "invite=" not in str(events)
