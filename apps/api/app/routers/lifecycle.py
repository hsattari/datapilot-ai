"""Data export, tenant deletion and user erasure (administrators only)."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from ..db import get_db
from ..deps import Principal, require_admin
from ..security import verify_password
from ..services import lifecycle, ratelimit, totp
from ..services.common import audit

router = APIRouter(prefix="/api/v1")
AUTH_LIMIT = ratelimit.by_ip("auth", "rate_auth_per_minute")
EXPORT_LIMIT = ratelimit.by_user("export", "rate_export_per_minute")


@router.get("/export/tenant", dependencies=[EXPORT_LIMIT])
def export_tenant(include_raw: bool = False, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    """Everything we hold for your organisation as a ZIP. Add include_raw=true for the original uploaded files."""
    data = lifecycle.build_export(db, p.tenant_id, include_raw)
    audit(db, p, "tenant.exported", include_raw=include_raw, bytes=len(data))
    db.commit()
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="datapilot-export-{date.today().isoformat()}.zip"'})


class DeleteTenantIn(BaseModel):
    confirm_name: str = Field(max_length=200)      # the organisation's exact name
    password: str = Field(max_length=200)
    code: str | None = Field(default=None, max_length=20)  # authenticator or recovery code if MFA is on


@router.post("/tenant/delete", dependencies=[AUTH_LIMIT])
def delete_tenant(body: DeleteTenantIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    """Irreversibly erase the organisation and all of its data. Needs the exact name, your password and (if enabled) an MFA code."""
    t = db.get(m.Tenant, p.tenant_id)
    if body.confirm_name != t.name:
        raise HTTPException(422, "The organisation name does not match")
    u = db.get(m.User, p.user_id)
    ok = verify_password(body.password, u.password_hash)
    if ok and u.mfa_enabled:
        secret = totp.decrypt_secret(u.mfa_secret or "")
        code = body.code or ""
        ok = bool(secret and totp.verify(secret, code, u.mfa_last_step) is not None) or totp.hash_recovery(code) in (u.mfa_recovery or [])
    if not ok:
        audit(db, p, "tenant.delete_refused")
        db.commit()
        raise HTTPException(401, "Invalid credentials")
    return {"deleted": True, "counts": lifecycle.delete_tenant(db, p.tenant_id, p.user_id)}


@router.delete("/users/{user_id}", status_code=204)
def erase_user(user_id: str, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    """Erase a person's identifying data (e-mail, password, MFA, sign-in links) and remove them from the organisation."""
    mem = db.scalar(select(m.TenantMembership).where(m.TenantMembership.tenant_id == p.tenant_id,
                                                     m.TenantMembership.user_id == user_id))
    u = db.get(m.User, user_id) if mem else None
    if mem is None or u is None:
        raise HTTPException(404, "Not found")
    if mem.role == "admin" and not u.disabled:
        admins = db.scalar(select(func.count()).select_from(m.TenantMembership)
                           .join(m.User, m.User.id == m.TenantMembership.user_id)
                           .where(m.TenantMembership.tenant_id == p.tenant_id, m.TenantMembership.role == "admin",
                                  m.User.disabled.is_(False)))
        if admins <= 1:
            raise HTTPException(422, "At least one active administrator is required")
    lifecycle.erase_user(db, p.tenant_id, user_id)
    audit(db, p, "user.erased", user_id=user_id)
    db.commit()
