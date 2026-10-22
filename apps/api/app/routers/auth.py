"""Sign-in hardening (lockout, MFA), password change and workspace user management."""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from ..config import get_settings
from ..db import get_db
from ..deps import Principal, get_principal, require_admin
from ..schemas import LoginIn
from ..security import create_token, hash_password, verify_password
from ..services import oidc, ratelimit, totp
from ..services.common import audit

router = APIRouter(prefix="/api/v1")
AUTH_LIMIT = ratelimit.by_ip("auth", "rate_auth_per_minute")
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")  # unknown emails cost the same time as wrong passwords


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def _tenant_of(db: Session, user_id: str) -> str | None:
    return db.scalar(select(m.TenantMembership.tenant_id).where(m.TenantMembership.user_id == user_id))


def _audit_user(db: Session, user_id: str, action: str, **detail) -> None:
    db.add(m.AuditEvent(tenant_id=_tenant_of(db, user_id), user_id=user_id, action=action, detail=detail))


@router.post("/auth/login", dependencies=[AUTH_LIMIT])
def login(body: LoginIn, db: Session = Depends(get_db)):
    s = get_settings()
    now = datetime.now(timezone.utc)
    user = db.scalar(select(m.User).where(m.User.email == body.email.lower()))
    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        raise HTTPException(401, "Invalid credentials")
    until = _aware(user.locked_until)
    if until and until > now:  # refuse even a correct password: no oracle for guessing during lockout
        raise HTTPException(429, "Too many failed attempts. Try again later.",
                            headers={"Retry-After": str(int((until - now).total_seconds()) + 1)})

    def fail():
        user.failed_logins = (user.failed_logins or 0) + 1
        _audit_user(db, user.id, "auth.failed")
        if user.failed_logins >= s.login_max_failures:
            user.locked_until = now + timedelta(minutes=s.login_lockout_minutes)
            user.failed_logins = 0
            _audit_user(db, user.id, "auth.locked", minutes=s.login_lockout_minutes)
        db.commit()
        raise HTTPException(401, "Invalid credentials")

    if not verify_password(body.password, user.password_hash) or user.disabled:
        fail()
    if user.mfa_enabled:
        if not body.code:
            raise HTTPException(401, "mfa_required")  # password was right: not counted as a failure
        secret = totp.decrypt_secret(user.mfa_secret or "")
        step = totp.verify(secret, body.code, user.mfa_last_step) if secret else None
        if step is not None:
            user.mfa_last_step = step
        else:
            h = totp.hash_recovery(body.code)
            if h in (user.mfa_recovery or []):
                user.mfa_recovery = [x for x in user.mfa_recovery if x != h]  # single use
                _audit_user(db, user.id, "auth.recovery_code_used")
            else:
                fail()
    user.failed_logins, user.locked_until = 0, None
    _audit_user(db, user.id, "auth.login")
    db.commit()
    return {"token": create_token(user.id)}


# ---------------------------------------------------------------- MFA
class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class DisableIn(BaseModel):
    password: str
    code: str = Field(min_length=1, max_length=32)


def _me(db: Session, p: Principal) -> m.User:
    return db.get(m.User, p.user_id)


@router.get("/auth/me")
def me(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = _me(db, p)
    return {"email": u.email, "role": p.role, "mfa_enabled": u.mfa_enabled,
            "recovery_codes_left": len(u.mfa_recovery or []) if u.mfa_enabled else 0}


@router.post("/auth/mfa/setup", dependencies=[AUTH_LIMIT])
def mfa_setup(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = _me(db, p)
    if u.mfa_enabled:
        raise HTTPException(409, "MFA is already enabled")
    secret = totp.new_secret()
    u.mfa_secret, u.mfa_last_step = totp.encrypt_secret(secret), None
    db.commit()
    return {"secret": secret, "otpauth_uri": totp.otpauth_uri(secret, u.email)}


@router.post("/auth/mfa/enable", dependencies=[AUTH_LIMIT])
def mfa_enable(body: CodeIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = _me(db, p)
    if u.mfa_enabled:
        raise HTTPException(409, "MFA is already enabled")
    secret = totp.decrypt_secret(u.mfa_secret or "") if u.mfa_secret else None
    if not secret:
        raise HTTPException(409, "Start MFA setup first")
    step = totp.verify(secret, body.code)
    if step is None:
        raise HTTPException(422, "That code is not valid. Check the time on your phone and try again.")
    codes = totp.new_recovery_codes()
    u.mfa_enabled, u.mfa_last_step, u.mfa_recovery = True, step, [totp.hash_recovery(c) for c in codes]
    audit(db, p, "auth.mfa_enabled")
    db.commit()
    return {"recovery_codes": codes, "note": "Store these somewhere safe. Each works once and they are not shown again."}


@router.post("/auth/mfa/disable", dependencies=[AUTH_LIMIT])
def mfa_disable(body: DisableIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = _me(db, p)
    if not u.mfa_enabled:
        raise HTTPException(409, "MFA is not enabled")
    secret = totp.decrypt_secret(u.mfa_secret or "")
    ok = verify_password(body.password, u.password_hash) and (
        (secret and totp.verify(secret, body.code, u.mfa_last_step) is not None)
        or totp.hash_recovery(body.code) in (u.mfa_recovery or []))
    if not ok:
        raise HTTPException(401, "Invalid credentials")
    u.mfa_enabled, u.mfa_secret, u.mfa_recovery, u.mfa_last_step = False, None, [], None
    audit(db, p, "auth.mfa_disabled")
    db.commit()
    return {"mfa_enabled": False}


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=72)


@router.post("/auth/change-password", dependencies=[AUTH_LIMIT])
def change_password(body: PasswordIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    u = _me(db, p)
    if not verify_password(body.current_password, u.password_hash):
        raise HTTPException(401, "Invalid credentials")
    u.password_hash = hash_password(body.new_password)
    audit(db, p, "auth.password_changed")
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- users
class UserIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Literal["admin", "analyst", "viewer"]
    password: str = Field(min_length=10, max_length=72)  # initial password: share it securely, user changes it after sign-in


class UserPatch(BaseModel):
    role: Literal["admin", "analyst", "viewer"] | None = None
    active: bool | None = None


def _user_json(u: m.User, role: str) -> dict:
    return {"id": u.id, "email": u.email, "role": role, "active": not u.disabled, "mfa_enabled": u.mfa_enabled}


@router.get("/users")
def list_users(p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(m.User, m.TenantMembership.role).join(m.TenantMembership, m.TenantMembership.user_id == m.User.id)
                      .where(m.TenantMembership.tenant_id == p.tenant_id).order_by(m.User.created_at)).all()
    return [_user_json(u, role) for u, role in rows]


@router.post("/users", status_code=201)
def add_user(body: UserIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(m.User).where(m.User.email == email)):
        raise HTTPException(409, "Email already registered")
    u = m.User(email=email, password_hash=hash_password(body.password))
    db.add(u)
    db.flush()
    db.add(m.TenantMembership(tenant_id=p.tenant_id, user_id=u.id, role=body.role))
    audit(db, p, "user.added", email=email, role=body.role)
    db.commit()
    return _user_json(u, body.role)


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    mem = db.scalar(select(m.TenantMembership).where(m.TenantMembership.tenant_id == p.tenant_id,
                                                     m.TenantMembership.user_id == user_id))
    u = db.get(m.User, user_id) if mem else None
    if mem is None or u is None:
        raise HTTPException(404, "Not found")
    new_role = body.role or mem.role
    new_active = (not u.disabled) if body.active is None else body.active
    if mem.role == "admin" and not u.disabled and (new_role != "admin" or not new_active):
        admins = db.scalar(select(func.count()).select_from(m.TenantMembership)
                           .join(m.User, m.User.id == m.TenantMembership.user_id)
                           .where(m.TenantMembership.tenant_id == p.tenant_id, m.TenantMembership.role == "admin",
                                  m.User.disabled.is_(False)))
        if admins <= 1:
            raise HTTPException(422, "At least one active administrator is required")
    mem.role, u.disabled = new_role, not new_active
    audit(db, p, "user.updated", user_id=user_id, role=new_role, active=new_active)
    db.commit()
    return _user_json(u, mem.role)


# ---------------------------------------------------------------- OIDC single sign-on
class OIDCExchangeIn(BaseModel):
    id_token: str = Field(min_length=20, max_length=8000)
    nonce: str | None = Field(default=None, max_length=200)


@router.get("/auth/oidc/config")
def oidc_config():
    """Public, non-secret settings the browser needs to start sign-in (e.g. MSAL authority and client id)."""
    s = get_settings()
    return {"enabled": oidc.enabled(), "issuer": s.oidc_issuer if oidc.enabled() else None,
            "client_id": s.oidc_client_id if oidc.enabled() else None}


@router.post("/auth/oidc/exchange", dependencies=[AUTH_LIMIT])
def oidc_exchange(body: OIDCExchangeIn, db: Session = Depends(get_db)):
    """Swap a verified ID token for a DataPilot access token.

    Only people an administrator has already added can sign in (no automatic account creation). The first sign-in
    links the provider identity to the account by e-mail; after that the account is matched by (issuer, subject) only.
    MFA is the identity provider's job on this path.
    """
    s = get_settings()
    if not oidc.enabled():
        raise HTTPException(404, "Single sign-on is not configured")
    try:
        claims = oidc.verify_id_token(body.id_token, body.nonce)
    except oidc.OIDCError:
        raise HTTPException(401, "Invalid token")
    issuer, subject = s.oidc_issuer, claims["sub"]
    ident = db.scalar(select(m.UserIdentity).where(m.UserIdentity.issuer == issuer, m.UserIdentity.subject == subject))
    if ident is not None:
        user = db.get(m.User, ident.user_id)
    else:
        email = str(claims.get("email") or claims.get("preferred_username") or "").lower()
        if not email or claims.get("email_verified") is False:
            raise HTTPException(403, "The identity provider did not supply a verified e-mail address")
        user = db.scalar(select(m.User).where(m.User.email == email))
        if user is None or db.scalar(select(m.UserIdentity).where(m.UserIdentity.user_id == user.id,
                                                                  m.UserIdentity.issuer == issuer)):
            # unknown person, or this account is already linked to a DIFFERENT identity: never re-link by e-mail
            raise HTTPException(403, "This account is not set up for single sign-on. Ask an administrator to add you.")
        db.add(m.UserIdentity(user_id=user.id, issuer=issuer, subject=subject))
        _audit_user(db, user.id, "auth.sso_linked", issuer=issuer)
    if user is None or user.disabled:
        raise HTTPException(401, "Invalid token")
    _audit_user(db, user.id, "auth.sso_login")
    db.commit()
    return {"token": create_token(user.id)}
