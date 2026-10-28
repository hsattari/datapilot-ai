"""Lineage endpoints (read-only, any signed-in role, always scoped to the caller's organisation)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, get_principal
from ..services import lineage

router = APIRouter(prefix="/api/v1")


@router.get("/lineage")
def get_lineage(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return lineage.graph(db, p.tenant_id)


@router.get("/lineage/metrics/{name}")
def get_metric_lineage(name: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    out = lineage.metric_trace(db, p.tenant_id, name)
    if out is None:
        raise HTTPException(404, "Not found")
    return out
