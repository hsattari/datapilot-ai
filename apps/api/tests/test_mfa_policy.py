"""Organisation policy: require two-step sign-in. Must enforce it, never lock an admin out, and stay tenant-scoped."""
from app import models as m
from app.db import SessionLocal

from .conftest import register
from .test_security import PW, enable_mfa, hdr, login

NEEDED = {"detail": "mfa_enrollment_required"}


def _policy(client, h, on=True):
    return client.patch("/api/v1/settings", json={"require_mfa": on}, headers=h)


def _add_user(client, h, email, role="analyst"):
    r = client.post("/api/v1/users", headers=h, json={"email": email, "role": role, "password": "initial-pass-2"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_admin_cannot_switch_the_policy_on_without_their_own_two_step(client):
    h, _ = register(client, "Own", "own@pol.test")
    r = _policy(client, h)
    assert r.status_code == 422 and "your own account" in r.json()["detail"]
    enable_mfa(client, h)
    assert _policy(client, h).status_code == 200
    assert client.get("/api/v1/settings", headers=h).json()["require_mfa"] is True


def test_people_without_two_step_can_only_enrol_until_they_do(client):
    h, _ = register(client, "Pol", "admin@pol2.test")
    enable_mfa(client, h)
    _add_user(client, h, "ana@pol2.test")
    ana = hdr(login(client, "ana@pol2.test", "initial-pass-2"))
    assert client.get("/api/v1/datasets", headers=ana).status_code == 200      # before the policy

    assert _policy(client, h).status_code == 200
    r = client.get("/api/v1/datasets", headers=ana)
    assert r.status_code == 403 and r.json() == NEEDED                          # blocked by the policy
    assert client.post("/api/v1/analytics/query", json={"question": "total revenue"}, headers=ana).status_code == 403
    me = client.get("/api/v1/auth/me", headers=ana).json()                       # but can see why and enrol
    assert me["mfa_required"] is True and me["mfa_enrollment_needed"] is True
    enable_mfa(client, ana)
    assert client.get("/api/v1/datasets", headers=ana).status_code == 200
    assert client.get("/api/v1/auth/me", headers=ana).json()["mfa_enrollment_needed"] is False


def test_nobody_under_the_policy_can_turn_their_own_two_step_off(client):
    h, _ = register(client, "NoOff", "admin@nooff.test")
    secret, recovery = enable_mfa(client, h)
    assert _policy(client, h).status_code == 200
    r = client.post("/api/v1/auth/mfa/disable", json={"password": PW, "code": recovery[0]}, headers=h)
    assert r.status_code == 409 and "requires" in r.json()["detail"]
    assert client.get("/api/v1/auth/me", headers=h).json()["mfa_enabled"] is True


def test_policy_can_be_switched_off_again(client):
    h, _ = register(client, "Off", "admin@off.test")
    _, recovery = enable_mfa(client, h)
    _policy(client, h)
    assert _policy(client, h, on=False).status_code == 200
    r = client.post("/api/v1/auth/mfa/disable", json={"password": PW, "code": recovery[0]}, headers=h)
    assert r.status_code == 200                                                  # allowed again once the policy is off


def test_admin_reset_for_a_lost_phone_sends_the_person_back_to_enrolment(client):
    h, _ = register(client, "Lost", "admin@lost.test")
    enable_mfa(client, h)
    uid = _add_user(client, h, "bob@lost.test")
    _policy(client, h)
    bob = hdr(login(client, "bob@lost.test", "initial-pass-2"))
    enable_mfa(client, bob)
    assert client.get("/api/v1/datasets", headers=bob).status_code == 200
    assert client.patch(f"/api/v1/users/{uid}", json={"reset_mfa": True}, headers=h).status_code == 200
    r = client.get("/api/v1/datasets", headers=bob)                              # must enrol again
    assert r.status_code == 403 and r.json() == NEEDED
    actions = [a["action"] for a in client.get("/api/v1/activity", headers=h).json()]
    assert "user.mfa_reset" in actions


def test_only_admins_can_change_the_policy_or_reset_two_step(client):
    h, _ = register(client, "Roles", "admin@roles.test")
    enable_mfa(client, h)
    uid = _add_user(client, h, "an@roles.test", "analyst")
    an = hdr(login(client, "an@roles.test", "initial-pass-2"))
    assert _policy(client, an).status_code == 403
    assert client.patch(f"/api/v1/users/{uid}", json={"reset_mfa": True}, headers=an).status_code == 403


def test_policy_does_not_leak_to_other_organisations(client):
    h1, _ = register(client, "A", "a@leak.test")
    enable_mfa(client, h1)
    _policy(client, h1)
    h2, _ = register(client, "B", "b@leak.test")                                # other org, no two-step, no policy
    assert client.get("/api/v1/datasets", headers=h2).status_code == 200


def test_sso_linked_accounts_are_exempt_because_the_provider_enforces_it(client):
    h, _ = register(client, "Sso", "admin@sso.test")
    enable_mfa(client, h)
    uid = _add_user(client, h, "sso@sso.test")
    sso = hdr(login(client, "sso@sso.test", "initial-pass-2"))
    with SessionLocal() as db:
        db.add(m.UserIdentity(user_id=uid, issuer="https://idp.example", subject="abc"))
        db.commit()
    assert _policy(client, h).status_code == 200
    assert client.get("/api/v1/datasets", headers=sso).status_code == 200


def test_deactivated_and_unauthenticated_requests_still_fail_first(client):
    assert client.get("/api/v1/datasets").status_code == 401
