"""Data-source health, refresh expectations and management summaries (always scoped to the caller's organisation)."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..db import get_db
from ..deps import Principal, get_principal, require_writer
from ..services import health, recommendations, summaries
from ..services.common import audit, owned

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def get_health(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return {"sources": health.sources(db, p.tenant_id), "issues": health.issues(db, p.tenant_id)}


class SourcePatch(BaseModel):
    expected_every_days: int | None = Field(default=None, ge=0, le=365)   # 0 or null = no expectation


@router.patch("/data-sources/{source_id}")
def patch_source(source_id: str, body: SourcePatch, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    s = owned(db, m.DataSource, source_id, p.tenant_id)
    s.expected_every_days = body.expected_every_days or None
    audit(db, p, "source.updated", source_id=s.id, expected_every_days=s.expected_every_days)
    db.commit()
    return {"id": s.id, "name": s.name, "expected_every_days": s.expected_every_days}


def _json(x: m.Summary, full: bool = True) -> dict:
    out = {"id": x.id, "kind": x.kind, "period_start": x.period_start, "period_end": x.period_end,
           "created_at": x.created_at.isoformat(), "headline": (x.content or {}).get("headline")}
    if full:
        out["content"] = x.content
    return out


@router.get("/summaries")
def list_summaries(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(m.Summary).where(m.Summary.tenant_id == p.tenant_id)
                      .order_by(m.Summary.period_start.desc(), m.Summary.created_at.desc()).limit(12)).all()
    return [_json(x, full=False) for x in rows]


@router.get("/summaries/{summary_id}")
def get_summary(summary_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _json(owned(db, m.Summary, summary_id, p.tenant_id))


@router.post("/summaries/generate", status_code=201)
def generate_summary(p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    """A summary of the last 7 days on request (the worker also creates one each week)."""
    end = date.today()
    row = summaries.upsert(db, p.tenant_id, "on_demand", end - timedelta(days=6), end)
    if row is None:
        raise HTTPException(422, "There is nothing to summarise yet: upload data and build the model first.")
    audit(db, p, "summary.generated", summary_id=row.id)
    db.commit()
    return _json(row)


@router.get("/recommendations")
def get_recommendations(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return recommendations.build(db, p)
