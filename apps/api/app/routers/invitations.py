"""Invitations: an administrator invites an e-mail address, the person sets their own password from a one-time link.

The token is random, shown once (in the e-mail, or to the administrator when no mail server is set up), stored only as
a SHA-256 hash, expires, and works once. A person who already has an account cannot be invited (409): accounts belong
to one organisation.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..config import get_settings
from ..db import get_db
from ..deps import Principal, require_admin
from ..security import create_token, hash_password
from ..services import mailer, quotas, ratelimit
from ..services.common import audit

router = APIRouter(prefix="/api/v1")
AUTH_LIMIT = ratelimit.by_ip("auth", "rate_auth_per_minute")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _status(i: m.Invitation) -> str:
    if i.accepted_at:
        return "accepted"
    if i.revoked_at:
        return "revoked"
    return "expired" if _aware(i.expires_at) < datetime.now(timezone.utc) else "pending"


def _json(i: m.Invitation) -> dict:
    return {"id": i.id, "email": i.email, "role": i.role, "status": _status(i), "expires_at": i.expires_at.isoformat(),
            "email_sent": i.email_sent}


class InviteIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Literal["admin", "analyst", "viewer"]


@router.post("/invitations", status_code=201)
def invite(body: InviteIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings()
    email = body.email.lower()
    if db.scalar(select(m.User).where(m.User.email == email)):
        raise HTTPException(409, "This e-mail address already has an account")
    quotas.enforce(db, p.tenant_id, "users")
    for old in db.scalars(select(m.Invitation).where(m.Invitation.tenant_id == p.tenant_id, m.Invitation.email == email,
                                                     m.Invitation.accepted_at.is_(None), m.Invitation.revoked_at.is_(None))):
        old.revoked_at = datetime.now(timezone.utc)   # a new invitation replaces the old link
    token = secrets.token_urlsafe(32)
    inv = m.Invitation(tenant_id=p.tenant_id, email=email, role=body.role, token_hash=_hash(token), invited_by=p.user_id,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=s.invite_ttl_days))
    base = (s.app_base_url or "").rstrip("/")
    link = f"{base}/?invite={token}"
    org = db.get(m.Tenant, p.tenant_id)
    inv.email_sent = mailer.send(email, f"You are invited to {org.name} on DataPilot AI",
                                 f"You have been invited to join {org.name} on DataPilot AI as {body.role}.\n\n"
                                 f"Open this link to choose your password (valid {s.invite_ttl_days} days, works once):\n{link}\n")
    db.add(inv)
    db.flush()
    audit(db, p, "invitation.created", email=email, role=body.role, emailed=inv.email_sent)
    db.commit()
    out = _json(inv)
    if not inv.email_sent:
        out["link"] = link   # no mail server: the administrator shares this link securely
    return out


@router.get("/invitations")
def list_invitations(p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(m.Invitation).where(m.Invitation.tenant_id == p.tenant_id).order_by(m.Invitation.created_at.desc()).limit(100))
    return [_json(i) for i in rows]


@router.delete("/invitations/{invitation_id}", status_code=204)
def revoke(invitation_id: str, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    i = db.get(m.Invitation, invitation_id)
    if i is None or i.tenant_id != p.tenant_id:
        raise HTTPException(404, "Not found")
    if _status(i) == "pending":
        i.revoked_at = datetime.now(timezone.utc)
        audit(db, p, "invitation.revoked", email=i.email)
        db.commit()


class AcceptIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=10, max_length=72)


def _valid(db: Session, token: str) -> m.Invitation:
    i = db.scalar(select(m.Invitation).where(m.Invitation.token_hash == _hash(token)))
    if i is None or _status(i) != "pending":
        raise HTTPException(410, "This invitation is no longer valid. Ask an administrator for a new one.")
    return i


@router.get("/auth/invitation", dependencies=[AUTH_LIMIT])
def preview(token: str, db: Session = Depends(get_db)):
    i = _valid(db, token)
    return {"email": i.email, "role": i.role, "organisation": db.get(m.Tenant, i.tenant_id).name}


@router.post("/auth/accept-invitation", dependencies=[AUTH_LIMIT])
def accept(body: AcceptIn, db: Session = Depends(get_db)):
    i = _valid(db, body.token)
    if db.scalar(select(m.User).where(m.User.email == i.email)):
        raise HTTPException(409, "This e-mail address already has an account")
    u = m.User(email=i.email, password_hash=hash_password(body.password))
    db.add(u)
    db.flush()
    db.add(m.TenantMembership(tenant_id=i.tenant_id, user_id=u.id, role=i.role))
    i.accepted_at = datetime.now(timezone.utc)
    db.add(m.AuditEvent(tenant_id=i.tenant_id, user_id=u.id, action="invitation.accepted", detail={"role": i.role}))
    db.commit()
    return {"token": create_token(u.id)}
