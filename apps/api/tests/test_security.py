"""Rate limits, lockout, MFA (TOTP + recovery codes), user management, headers, production guard."""
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import models as m
from app.config import get_settings
from app.db import SessionLocal
from app.main import check_production_config
from app.services import ratelimit, totp

from .conftest import register

PW = "correct-horse-1"


def login(client, email, password=PW, code=None):
    body = {"email": email, "password": password, **({"code": code} if code else {})}
    return client.post("/api/v1/auth/login", json=body)


def hdr(r):
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ------------------------------------------------------------------ TOTP primitives
def test_totp_matches_rfc6238_vectors():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # ASCII "12345678901234567890"
    assert totp.code_at(secret, 59 // 30, digits=8) == "94287082"
    assert totp.code_at(secret, 1111111109 // 30, digits=8) == "07081804"
    assert totp.code_at(secret, 2000000000 // 30, digits=8) == "69279037"


def test_totp_window_and_replay():
    s = totp.new_secret()
    now = 1_700_000_000.0
    step = totp.current_step(now)
    good = totp.code_at(s, step)
    assert totp.verify(s, good, now=now) == step
    assert totp.verify(s, totp.code_at(s, step - 1), now=now) == step - 1      # one step of clock drift
    assert totp.verify(s, totp.code_at(s, step + 2), now=now) is None          # too far
    assert totp.verify(s, good, last_step=step, now=now) is None               # replay of the same step
    assert totp.verify(s, "12345", now=now) is None and totp.verify(s, "abcdef", now=now) is None


# ------------------------------------------------------------------ rate limiting
def test_auth_endpoints_are_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_auth_per_minute", 3)
    register(client, "RL", "rl@x.test")
    codes = [login(client, "nobody@x.test").status_code for _ in range(4)]
    assert codes[:2] == [401, 401] and codes[-1] == 429  # 1 register + 2 logins used the budget of 3
    r = login(client, "nobody@x.test")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_query_limit_is_per_user(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_query_per_minute", 2)
    h1, _ = register(client, "One", "q1@x.test")
    h2, _ = register(client, "Two", "q2@x.test")
    q = lambda h: client.post("/api/v1/analytics/query", json={"question": "gross profit"}, headers=h).status_code  # noqa: E731
    assert [q(h1), q(h1), q(h1)][-1] == 429
    assert q(h2) != 429  # another user is unaffected


def test_upload_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_upload_per_minute", 1)
    h, _ = register(client, "Up", "up@x.test")
    sid = client.post("/api/v1/data-sources", json={"name": "s"}, headers=h).json()["id"]
    up = lambda: client.post(f"/api/v1/data-sources/{sid}/uploads", headers=h,  # noqa: E731
                             files={"file": ("a.csv", b"order_id,order_date,net_sales\nA,2026-01-01,1\n", "text/csv")}).status_code
    assert up() == 202 and up() == 429


def test_limiter_can_be_switched_off_and_window_expires(monkeypatch):
    assert ratelimit.check("k", 1, window=0.05) == 0.0
    assert ratelimit.check("k", 1, window=0.05) > 0
    time.sleep(0.07)
    assert ratelimit.check("k", 1, window=0.05) == 0.0


# ------------------------------------------------------------------ lockout
def test_lockout_after_repeated_failures_and_recovery(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_auth_per_minute", 0)  # isolate lockout from the IP limiter
    register(client, "Lock", "lock@x.test")
    for _ in range(5):
        assert login(client, "lock@x.test", "wrong-password-1").status_code == 401
    r = login(client, "lock@x.test")  # correct password, but the account is locked
    assert r.status_code == 429 and "Retry-After" in r.headers
    with SessionLocal() as db:
        actions = [a.action for a in db.scalars(select(m.AuditEvent))]
        assert "auth.locked" in actions and actions.count("auth.failed") == 5
        u = db.scalar(select(m.User).where(m.User.email == "lock@x.test"))
        u.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)  # lockout expires
        db.commit()
    assert login(client, "lock@x.test").status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(m.User.failed_logins).where(m.User.email == "lock@x.test")) == 0


def test_success_resets_failure_counter(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_auth_per_minute", 0)
    register(client, "Reset", "reset@x.test")
    for _ in range(4):
        login(client, "reset@x.test", "wrong-password-1")
    assert login(client, "reset@x.test").status_code == 200
    for _ in range(4):
        assert login(client, "reset@x.test", "wrong-password-1").status_code == 401  # not locked: counter restarted


def test_unknown_email_and_wrong_password_look_identical(client):
    register(client, "Same", "same@x.test")
    a, b = login(client, "same@x.test", "wrong-password-1"), login(client, "ghost@x.test")
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


# ------------------------------------------------------------------ MFA
def enable_mfa(client, h):
    setup = client.post("/api/v1/auth/mfa/setup", headers=h).json()
    secret = setup["secret"]
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and secret in setup["otpauth_uri"]
    r = client.post("/api/v1/auth/mfa/enable", json={"code": totp.code_at(secret, totp.current_step())}, headers=h)
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def test_mfa_full_flow(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_auth_per_minute", 0)
    r = client.post("/api/v1/tenants", json={"organisation": "MFA", "email": "mfa@x.test", "password": PW})
    h = hdr(r)
    assert client.get("/api/v1/auth/me", headers=h).json()["mfa_enabled"] is False
    secret, codes = enable_mfa(client, h)
    assert len(codes) == 8 and client.get("/api/v1/auth/me", headers=h).json()["recovery_codes_left"] == 8

    assert login(client, "mfa@x.test").json() == {"detail": "mfa_required"}            # password alone is not enough
    assert login(client, "mfa@x.test", code="000000").status_code == 401
    # the code used to enable MFA cannot be replayed to sign in; use the next step's code
    nxt = totp.code_at(secret, totp.current_step() + 1)
    ok = login(client, "mfa@x.test", code=nxt)
    assert ok.status_code == 200 and "token" in ok.json()
    assert login(client, "mfa@x.test", code=nxt).status_code == 401                   # replay rejected

    rc = login(client, "mfa@x.test", code=codes[0])                                    # recovery code works once
    assert rc.status_code == 200
    assert login(client, "mfa@x.test", code=codes[0]).status_code == 401
    assert client.get("/api/v1/auth/me", headers=hdr(rc)).json()["recovery_codes_left"] == 7


def test_mfa_secret_is_encrypted_at_rest_and_setup_is_not_repeatable(client):
    h, _ = register(client, "Enc", "enc@x.test")
    secret, _ = enable_mfa(client, h)
    with SessionLocal() as db:
        u = db.scalar(select(m.User).where(m.User.email == "enc@x.test"))
        assert u.mfa_secret and secret not in u.mfa_secret and totp.decrypt_secret(u.mfa_secret) == secret
        assert all(len(x) == 64 for x in u.mfa_recovery)  # recovery codes stored only as hashes
    assert client.post("/api/v1/auth/mfa/setup", headers=h).status_code == 409
    assert client.post("/api/v1/auth/mfa/enable", json={"code": "123456"}, headers=h).status_code == 409


def test_mfa_enable_needs_setup_and_valid_code(client):
    h, _ = register(client, "Bad", "badmfa@x.test")
    assert client.post("/api/v1/auth/mfa/enable", json={"code": "123456"}, headers=h).status_code == 409
    client.post("/api/v1/auth/mfa/setup", headers=h)
    assert client.post("/api/v1/auth/mfa/enable", json={"code": "000000"}, headers=h).status_code == 422
    assert client.get("/api/v1/auth/me", headers=h).json()["mfa_enabled"] is False


def test_mfa_disable_needs_password_and_code(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_auth_per_minute", 0)
    r = client.post("/api/v1/tenants", json={"organisation": "Dis", "email": "dis@x.test", "password": PW})
    h = hdr(r)
    secret, codes = enable_mfa(client, h)
    assert client.post("/api/v1/auth/mfa/disable", json={"password": "wrong-password-1", "code": codes[0]}, headers=h).status_code == 401
    assert client.post("/api/v1/auth/mfa/disable", json={"password": PW, "code": "000000"}, headers=h).status_code == 401
    assert client.post("/api/v1/auth/mfa/disable", json={"password": PW, "code": codes[0]}, headers=h).status_code == 200
    assert login(client, "dis@x.test").status_code == 200  # no code needed any more


def test_change_password(client):
    h, _ = register(client, "Pw", "pw@x.test")
    assert client.post("/api/v1/auth/change-password", json={"current_password": "nope-nope-1", "new_password": "a-new-password-1"}, headers=h).status_code == 401
    assert client.post("/api/v1/auth/change-password", json={"current_password": PW, "new_password": "short"}, headers=h).status_code == 422
    assert client.post("/api/v1/auth/change-password", json={"current_password": PW, "new_password": "a-new-password-1"}, headers=h).status_code == 200
    assert login(client, "pw@x.test").status_code == 401
    assert login(client, "pw@x.test", "a-new-password-1").status_code == 200


# ------------------------------------------------------------------ user management
def test_admin_manages_users_and_roles_apply_immediately(client):
    h, t = register(client, "Team", "boss@x.test")
    r = client.post("/api/v1/users", json={"email": "Ana@X.test", "role": "analyst", "password": "analyst-pass-1"}, headers=h)
    assert r.status_code == 201 and r.json()["email"] == "ana@x.test"
    assert client.post("/api/v1/users", json={"email": "ana@x.test", "role": "viewer", "password": "viewer-pass-12"}, headers=h).status_code == 409
    ana = hdr(login(client, "ana@x.test", "analyst-pass-1"))
    assert client.post("/api/v1/data-sources", json={"name": "s"}, headers=ana).status_code == 201   # analyst can write
    assert client.get("/api/v1/users", headers=ana).status_code == 403                              # ...but not administer
    uid = r.json()["id"]
    assert client.patch(f"/api/v1/users/{uid}", json={"role": "viewer"}, headers=h).json()["role"] == "viewer"
    assert client.post("/api/v1/data-sources", json={"name": "s2"}, headers=ana).status_code == 403  # same token, new role
    assert {u["email"] for u in client.get("/api/v1/users", headers=h).json()} == {"boss@x.test", "ana@x.test"}


def test_deactivated_user_loses_access_immediately(client):
    h, _ = register(client, "Off", "off@x.test")
    uid = client.post("/api/v1/users", json={"email": "gone@x.test", "role": "viewer", "password": "viewer-pass-12"}, headers=h).json()["id"]
    gone = hdr(login(client, "gone@x.test", "viewer-pass-12"))
    assert client.get("/api/v1/datasets", headers=gone).status_code == 200
    assert client.patch(f"/api/v1/users/{uid}", json={"active": False}, headers=h).status_code == 200
    assert client.get("/api/v1/datasets", headers=gone).status_code == 401           # existing token stops working
    assert login(client, "gone@x.test", "viewer-pass-12").status_code == 401
    assert client.patch(f"/api/v1/users/{uid}", json={"active": True}, headers=h).status_code == 200
    assert login(client, "gone@x.test", "viewer-pass-12").status_code == 200


def test_last_admin_cannot_be_removed_and_users_are_tenant_scoped(client):
    h, t = register(client, "Solo", "solo@x.test")
    me_id = next(u["id"] for u in client.get("/api/v1/users", headers=h).json())
    assert client.patch(f"/api/v1/users/{me_id}", json={"role": "viewer"}, headers=h).status_code == 422
    assert client.patch(f"/api/v1/users/{me_id}", json={"active": False}, headers=h).status_code == 422
    client.post("/api/v1/users", json={"email": "two@x.test", "role": "admin", "password": "admin-pass-123"}, headers=h)
    assert client.patch(f"/api/v1/users/{me_id}", json={"role": "viewer"}, headers=h).status_code == 200  # a second admin exists
    h2, _ = register(client, "Other", "other@x.test")
    assert client.patch(f"/api/v1/users/{me_id}", json={"role": "admin"}, headers=h2).status_code == 404
    assert all(u["email"] == "other@x.test" for u in client.get("/api/v1/users", headers=h2).json())


# ------------------------------------------------------------------ headers & config
def test_security_headers(client):
    r = client.get("/api/v1/datasets")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store" and "default-src 'none'" in r.headers["Content-Security-Policy"]
    assert client.get("/healthz").headers["X-Content-Type-Options"] == "nosniff"


def test_production_refuses_development_secrets(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "environment", "production")
    monkeypatch.setattr(s, "jwt_secret", "change-me-in-production")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        check_production_config()
    monkeypatch.setattr(s, "jwt_secret", "x" * 40)
    with pytest.raises(RuntimeError, match="MFA_ENCRYPTION_KEY"):
        check_production_config()
    monkeypatch.setattr(s, "mfa_encryption_key", "y" * 40)
    with pytest.raises(RuntimeError, match="MALWARE_SCAN"):
        check_production_config()  # production must scan uploads
    monkeypatch.setattr(s, "malware_scan", "clamav")
    monkeypatch.setattr(s, "malware_scan_fail_open", True)
    with pytest.raises(RuntimeError, match="FAIL_OPEN"):
        check_production_config()
    monkeypatch.setattr(s, "malware_scan_fail_open", False)
    check_production_config()  # now fine
    monkeypatch.setattr(s, "environment", "development")
    monkeypatch.setattr(s, "jwt_secret", "change-me-in-production")
    check_production_config()  # development is allowed to use defaults
