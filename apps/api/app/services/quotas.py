"""Plan limits (cost control). Checked where cost is incurred: uploads, rows loaded, questions, people, documents.

The numbers below are PLACEHOLDERS for the proposed tiers (proposal section 18), not validated prices or limits.
0 means unlimited. The plan of an organisation is set by the service owner (database / admin tooling), never by the
customer, so there is deliberately no endpoint for changing it.
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from ..config import get_settings

WARN_AT = 0.8
PLANS: dict[str, dict[str, int]] = {
    #                 uploads/30d  rows/30d   questions/24h  people  documents
    "starter":    dict(uploads=50,    rows=500_000,    questions=100,    users=5,   documents=50),
    "growth":     dict(uploads=500,   rows=5_000_000,  questions=1_000,  users=25,  documents=500),
    "advanced":   dict(uploads=5_000, rows=50_000_000, questions=10_000, users=100, documents=5_000),
    "enterprise": dict(uploads=0,     rows=0,          questions=0,      users=0,   documents=0),
}
LABELS = {
    "uploads": ("files uploaded in the last 30 days", "uploads"),
    "rows": ("rows loaded in the last 30 days", "rows"),
    "questions": ("questions asked in the last 24 hours", "questions"),
    "users": ("people in the organisation", "people"),
    "documents": ("knowledge documents", "documents"),
}


def plan_of(tenant: m.Tenant | None) -> str:
    name = (tenant.plan if tenant is not None else None) or get_settings().default_plan
    return name if name in PLANS else "starter"


def limits_for(tenant: m.Tenant | None) -> dict[str, int]:
    return PLANS[plan_of(tenant)]


def used(db: Session, tenant_id: str) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    d30, d1 = now - timedelta(days=30), now - timedelta(days=1)
    return {
        "uploads": db.scalar(select(func.count()).select_from(m.SourceFile).where(
            m.SourceFile.tenant_id == tenant_id, m.SourceFile.created_at >= d30)) or 0,
        "rows": db.scalar(select(func.coalesce(func.sum(m.IngestionJob.rows_loaded), 0)).where(
            m.IngestionJob.tenant_id == tenant_id, m.IngestionJob.created_at >= d30)) or 0,
        "questions": db.scalar(select(func.count()).select_from(m.AnalyticalQuery).where(
            m.AnalyticalQuery.tenant_id == tenant_id, m.AnalyticalQuery.created_at >= d1)) or 0,
        "users": db.scalar(select(func.count()).select_from(m.TenantMembership).where(
            m.TenantMembership.tenant_id == tenant_id)) or 0,
        "documents": db.scalar(select(func.count()).select_from(m.Document).where(
            m.Document.tenant_id == tenant_id)) or 0,
    }


def snapshot(db: Session, tenant_id: str) -> dict:
    t = db.get(m.Tenant, tenant_id)
    lim, use = limits_for(t), used(db, tenant_id)
    warnings = []
    for k, cap in lim.items():
        if cap and use[k] >= cap * WARN_AT:
            what = LABELS[k][0]
            warnings.append({"item": k, "used": use[k], "limit": cap, "reached": use[k] >= cap,
                             "message": (f"Limit reached: {use[k]} of {cap} {what}." if use[k] >= cap
                                         else f"{use[k]} of {cap} {what} used.")})
    return {"plan": plan_of(t), "limits": lim, "used": use, "warnings": warnings}


def enforce(db: Session, tenant_id: str, item: str) -> None:
    """Refuse (429) when this action would go past the plan. Raises nothing for unlimited plans."""
    cap = limits_for(db.get(m.Tenant, tenant_id)).get(item, 0)
    if not cap:
        return
    current = used(db, tenant_id)[item]
    if current >= cap:
        what = LABELS[item][0]
        raise HTTPException(429, f"Plan limit reached: {current} of {cap} {what}. "
                                 "Ask your administrator to review the plan, or try again later if the limit is time-based.")
