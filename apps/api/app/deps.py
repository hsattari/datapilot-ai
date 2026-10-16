"""Authentication and tenant resolution.

The tenant is ALWAYS resolved server-side from the authenticated user's membership.
A browser-supplied tenant ID (header, query, body) is never trusted.
"""
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import TenantMembership
from .security import decode_token

bearer = HTTPBearer(auto_error=False)

WRITE_ROLES = {"admin", "analyst"}


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    role: str


def get_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if creds is None:
        raise HTTPException(401, "Not authenticated")
    try:
        user_id = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    m = db.scalar(select(TenantMembership).where(TenantMembership.user_id == user_id))
    if m is None:
        raise HTTPException(403, "No tenant membership")
    return Principal(user_id=user_id, tenant_id=m.tenant_id, role=m.role)


def require_writer(p: Principal = Depends(get_principal)) -> Principal:
    if p.role not in WRITE_ROLES:
        raise HTTPException(403, "Insufficient role")
    return p


def require_admin(p: Principal = Depends(get_principal)) -> Principal:
    if p.role != "admin":
        raise HTTPException(403, "Admin role required")
    return p
