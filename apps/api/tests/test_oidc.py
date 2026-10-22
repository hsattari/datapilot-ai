"""OIDC sign-in against a fake identity provider (locally generated RSA keys). Not tested against real Entra ID."""
import base64
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import models as m
from app.config import get_settings
from app.db import SessionLocal
from app.services import oidc

from .conftest import register
from .test_workspace import add_viewer  # noqa: F401

ISSUER, CLIENT = "https://login.example.test/tenant-1/v2.0", "client-abc"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY, OTHER_KEY = _key(), _key()


def jwk_for(key, kid):
    d = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**d, "kid": kid, "use": "sig", "alg": "RS256"}


@pytest.fixture()
def idp(monkeypatch):
    s = get_settings()
    for k, v in dict(oidc_issuer=ISSUER, oidc_client_id=CLIENT, oidc_required_tid="", oidc_jwks_uri="").items():
        monkeypatch.setattr(s, k, v)
    state = {"keys": [jwk_for(KEY, "k1")], "fetches": 0}

    def fake_fetch():
        state["fetches"] += 1
        return {"keys": state["keys"]}

    monkeypatch.setattr(oidc, "fetch_jwks", fake_fetch)
    oidc.reset_cache()
    return state


def token(sub="sub-1", email="ann@acme.test", key=KEY, kid="k1", **over):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": CLIENT, "sub": sub, "email": email, "iat": now, "exp": now + 300, **over}
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def exchange(client, tok, nonce=None):
    return client.post("/api/v1/auth/oidc/exchange", json={"id_token": tok, **({"nonce": nonce} if nonce else {})})


def tenant_with_user(client, email="ann@acme.test"):
    h, t = register(client, "Acme", "admin@acme.test")
    client.post("/api/v1/users", json={"email": email, "role": "analyst", "password": "initial-pass-1"}, headers=h)
    return h, t


def test_not_configured_returns_404(client):
    assert client.get("/api/v1/auth/oidc/config").json() == {"enabled": False, "issuer": None, "client_id": None}
    assert exchange(client, "x" * 30).status_code == 404


def test_config_is_public_and_has_no_secrets(client, idp):
    c = client.get("/api/v1/auth/oidc/config").json()
    assert c == {"enabled": True, "issuer": ISSUER, "client_id": CLIENT}


def test_first_sign_in_links_provisioned_user_then_matches_by_subject(client, idp):
    h, _ = tenant_with_user(client)
    r = exchange(client, token())
    assert r.status_code == 200, r.text
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).json()
    assert me["email"] == "ann@acme.test" and me["role"] == "analyst"
    # the e-mail claim changes (or is spoofed): the SAME subject still maps to the same account
    r2 = exchange(client, token(email="someone.else@evil.test"))
    assert r2.status_code == 200
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {r2.json()['token']}"}).json()["email"] == "ann@acme.test"
    acts = [a["action"] for a in client.get("/api/v1/activity", headers=h).json()]
    assert "auth.sso_linked" in acts and acts.count("auth.sso_login") == 2


def test_unprovisioned_person_cannot_sign_in(client, idp):
    tenant_with_user(client)
    r = exchange(client, token(sub="sub-9", email="stranger@x.test"))
    assert r.status_code == 403
    with SessionLocal() as db:
        assert db.query(m.UserIdentity).count() == 0  # nothing was created or linked


def test_account_linked_to_one_identity_cannot_be_taken_over_by_email(client, idp):
    tenant_with_user(client)
    assert exchange(client, token(sub="sub-1")).status_code == 200
    assert exchange(client, token(sub="attacker-sub", email="ann@acme.test")).status_code == 403


@pytest.mark.parametrize("mutate", [
    dict(aud="other-client"), dict(iss="https://evil.test/v2.0"), dict(exp=int(time.time()) - 3600),
    dict(sub=None), dict(email_verified=False),
])
def test_bad_claims_are_rejected(client, idp, mutate):
    tenant_with_user(client)
    assert exchange(client, token(**mutate)).status_code in (401, 403)
    with SessionLocal() as db:
        assert db.query(m.UserIdentity).count() == 0


def test_signature_and_algorithm_attacks_are_rejected(client, idp):
    tenant_with_user(client)
    assert exchange(client, token(key=OTHER_KEY)).status_code == 401  # signed by a different key, same kid
    assert exchange(client, token(kid="unknown")).status_code == 401
    now = int(time.time())
    body = {"iss": ISSUER, "aud": CLIENT, "sub": "s", "email": "ann@acme.test", "exp": now + 300}
    none_tok = jwt.encode(body, None, algorithm="none", headers={"kid": "k1"})  # alg=none
    assert exchange(client, none_tok).status_code == 401
    # algorithm confusion: sign with HS256 using the PUBLIC key bytes as the secret
    # (PyJWT refuses to build this token itself, so craft it by hand the way an attacker would.)
    import hashlib, hmac
    from cryptography.hazmat.primitives import serialization
    pem = KEY.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def b64(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    signing_input = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "k1"}).encode()) + "." + b64(json.dumps(body).encode())
    hs = signing_input + "." + b64(hmac.new(pem, signing_input.encode(), hashlib.sha256).digest())
    assert exchange(client, hs).status_code == 401
    assert exchange(client, "not.a.jwt" + "x" * 20).status_code == 401


def test_nonce_is_enforced_when_supplied(client, idp):
    tenant_with_user(client)
    tok = token(nonce="n-123")
    assert exchange(client, tok, nonce="wrong").status_code == 401
    assert exchange(client, tok, nonce="n-123").status_code == 200


def test_required_tenant_id(client, idp, monkeypatch):
    tenant_with_user(client)
    monkeypatch.setattr(get_settings(), "oidc_required_tid", "tid-1")
    assert exchange(client, token(tid="tid-OTHER")).status_code == 401
    assert exchange(client, token(tid="tid-1")).status_code == 200


def test_deactivated_user_cannot_sign_in_by_sso(client, idp):
    h, _ = tenant_with_user(client)
    assert exchange(client, token()).status_code == 200
    uid = next(u["id"] for u in client.get("/api/v1/users", headers=h).json() if u["email"] == "ann@acme.test")
    assert client.patch(f"/api/v1/users/{uid}", json={"active": False}, headers=h).status_code == 200
    assert exchange(client, token()).status_code == 401


def test_key_rotation_is_picked_up_and_keys_are_cached(client, idp):
    tenant_with_user(client)
    assert exchange(client, token()).status_code == 200
    assert exchange(client, token()).status_code == 200
    assert idp["fetches"] == 1  # cached
    idp["keys"] = [jwk_for(KEY, "k1"), jwk_for(OTHER_KEY, "k2")]  # provider publishes a new key
    oidc._cache["at"] -= 120  # the minimum refresh interval has passed
    assert exchange(client, token(key=OTHER_KEY, kid="k2")).status_code == 200
    assert idp["fetches"] == 2


def test_unknown_kid_does_not_let_attackers_hammer_the_provider(client, idp):
    tenant_with_user(client)
    exchange(client, token())
    base = idp["fetches"]
    for _ in range(5):
        assert exchange(client, token(kid="bogus")).status_code == 401
    assert idp["fetches"] == base  # forced refresh is limited to once per minute


def test_provider_outage_fails_closed(client, idp, monkeypatch):
    tenant_with_user(client)

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(oidc, "fetch_jwks", boom)
    oidc.reset_cache()
    assert exchange(client, token()).status_code == 401


def test_exchange_is_rate_limited(client, idp, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_auth_per_minute", 3)
    codes = [exchange(client, "x" * 30).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]
