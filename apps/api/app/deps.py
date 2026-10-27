"""Authentication and tenant resolution.

The tenant is ALWAYS resolved server-side from the authenticated user's membership.
A browser-supplied tenant ID (header, query, body) is never trusted.
"""
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import observability
from .db import get_db
from .models import Tenant, TenantMembership, User, UserIdentity
from .security import decode_token

bearer = HTTPBearer(auto_error=False)

WRITE_ROLES = {"admin", "analyst"}

# When the organisation requires two-step sign-in, a person without it can reach only these until they enrol.
MFA_ENROLMENT_PATHS = {"/api/v1/auth/me", "/api/v1/auth/mfa/setup", "/api/v1/auth/mfa/enable"}


def mfa_enrolment_needed(db: Session, user: User, tenant_id: str) -> bool:
    """True if the organisation requires two-step sign-in and this person (password account) has not set it up.
    Accounts linked to a company identity provider are exempt: the provider enforces its own verification."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or not tenant.require_mfa or user.mfa_enabled:
        return False
    return db.scalar(select(UserIdentity.id).where(UserIdentity.user_id == user.id)) is None


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    role: str


def get_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if creds is None:
        raise HTTPException(401, "Not authenticated")
    try:
        user_id = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    user = db.get(User, user_id)
    if user is None or user.disabled:  # checked on every request, so deactivation takes effect immediately
        raise HTTPException(401, "Invalid or expired token")
    m = db.scalar(select(TenantMembership).where(TenantMembership.user_id == user_id))
    if m is None:
        raise HTTPException(403, "No tenant membership")
    if request.url.path not in MFA_ENROLMENT_PATHS and mfa_enrolment_needed(db, user, m.tenant_id):
        raise HTTPException(403, "mfa_enrollment_required")
    observability.bind(tenant_id=m.tenant_id, user_id=user_id)
    return Principal(user_id=user_id, tenant_id=m.tenant_id, role=m.role)


def require_writer(p: Principal = Depends(get_principal)) -> Principal:
    if p.role not in WRITE_ROLES:
        raise HTTPException(403, "Insufficient role")
    return p


def require_admin(p: Principal = Depends(get_principal)) -> Principal:
    if p.role != "admin":
        raise HTTPException(403, "Admin role required")
    return p
