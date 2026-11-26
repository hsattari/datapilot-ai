"""Separate workspaces. A workspace is a fully isolated data space (its own sources, metrics, dashboards, people, audit
trail, quotas): technically its own tenant. One person can belong to several and switch between them; every request is
still resolved from membership, so the token's workspace choice can never reach a workspace the person is not in.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from ..config import get_settings
from ..db import get_db
from ..deps import Principal, get_principal
from ..security import create_token
from ..services import semantic
from ..services.common import audit

router = APIRouter(prefix="/api/v1")


@router.get("/workspaces")
def list_workspaces(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(select(m.Tenant, m.TenantMembership.role).join(m.TenantMembership, m.TenantMembership.tenant_id == m.Tenant.id)
                      .where(m.TenantMembership.user_id == p.user_id).order_by(m.Tenant.created_at)).all()
    return [{"id": t.id, "name": t.name, "role": role, "current": t.id == p.tenant_id} for t, role in rows]


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    cfg = get_settings()
    owned = db.scalar(select(func.count()).select_from(m.TenantMembership)
                      .where(m.TenantMembership.user_id == p.user_id, m.TenantMembership.role == "admin"))
    if owned >= cfg.max_workspaces_per_user:
        raise HTTPException(429, f"You can administer at most {cfg.max_workspaces_per_user} workspaces")
    cur = db.get(m.Tenant, p.tenant_id)
    t = m.Tenant(name=body.name.strip(), currency=cur.currency, timezone=cur.timezone, auto_model=cfg.auto_model_default,
                 plan=cur.plan, require_mfa=cur.require_mfa)   # same plan and sign-in policy: no way around limits by splitting
    db.add(t)
    db.flush()
    db.add(m.TenantMembership(tenant_id=t.id, user_id=p.user_id, role="admin"))
    semantic.seed_metrics(db, t.id)
    db.add(m.AuditEvent(tenant_id=t.id, user_id=p.user_id, action="workspace.created", detail={"from": p.tenant_id}))
    db.commit()
    return {"id": t.id, "name": t.name, "token": create_token(p.user_id, t.id)}


class SwitchIn(BaseModel):
    tenant_id: str


@router.post("/workspaces/switch")
def switch_workspace(body: SwitchIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    mem = db.scalar(select(m.TenantMembership).where(m.TenantMembership.user_id == p.user_id,
                                                     m.TenantMembership.tenant_id == body.tenant_id))
    if mem is None:
        raise HTTPException(404, "Not found")   # same answer for "does not exist" and "not yours"
    audit(db, Principal(p.user_id, mem.tenant_id, mem.role), "workspace.switched")
    db.commit()
    return {"token": create_token(p.user_id, mem.tenant_id), "role": mem.role}
