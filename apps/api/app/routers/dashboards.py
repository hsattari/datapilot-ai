"""Dashboards: named sets of tiles built from approved metrics and saved reports (organisation-scoped)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..db import get_db
from ..deps import Principal, get_principal, require_writer
from ..services import insights
from ..services.common import audit, owned, run_plan
from ..services.query_engine import Plan, QueryRejected

router = APIRouter(prefix="/api/v1")
MAX_TILES = 12
MAX_DASHBOARDS = 20


class Tile(BaseModel):
    kind: Literal["metric", "report"]
    metric: str | None = Field(default=None, max_length=100)
    report_id: str | None = Field(default=None, max_length=36)


class DashboardIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    tiles: list[Tile] = Field(min_length=1, max_length=MAX_TILES)


def _validate(db: Session, p: Principal, tiles: list[Tile]) -> list[dict]:
    """Every tile must point at something in THIS organisation; anything else is refused up front."""
    out = []
    for t in tiles:
        if t.kind == "metric":
            if not t.metric or db.scalar(select(m.Metric.id).where(m.Metric.tenant_id == p.tenant_id,
                                                                    m.Metric.name == t.metric).limit(1)) is None:
                raise HTTPException(422, f"Unknown metric: {t.metric}")
            out.append({"kind": "metric", "metric": t.metric})
        else:
            if not t.report_id:
                raise HTTPException(422, "A report tile needs a report")
            owned(db, m.Report, t.report_id, p.tenant_id)      # 404 if it is another organisation's
            out.append({"kind": "report", "report_id": t.report_id})
    return out


def _json(d: m.Dashboard) -> dict:
    return {"id": d.id, "title": d.title, "tiles": d.tiles, "created_at": d.created_at.isoformat()}


@router.get("/dashboards")
def list_dashboards(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return [_json(d) for d in db.scalars(select(m.Dashboard).where(m.Dashboard.tenant_id == p.tenant_id)
                                         .order_by(m.Dashboard.created_at))]


@router.post("/dashboards", status_code=201)
def create_dashboard(body: DashboardIn, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    n = db.scalar(select(m.Dashboard.id).where(m.Dashboard.tenant_id == p.tenant_id).offset(MAX_DASHBOARDS - 1).limit(1))
    if n is not None:
        raise HTTPException(429, f"You can have up to {MAX_DASHBOARDS} dashboards; delete one first.")
    d = m.Dashboard(tenant_id=p.tenant_id, title=body.title, tiles=_validate(db, p, body.tiles), created_by=p.user_id)
    db.add(d)
    audit(db, p, "dashboard.created", title=body.title, tiles=len(d.tiles))
    db.commit()
    return _json(d)


@router.post("/dashboards/auto", status_code=201)
def auto_dashboard(p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    """An "Overview" dashboard with one tile per approved metric. Calling it again refreshes that dashboard."""
    names = [x.name for x in db.scalars(select(m.Metric).where(m.Metric.tenant_id == p.tenant_id,
                                                               m.Metric.status == "approved").order_by(m.Metric.name))]
    if not names:
        raise HTTPException(422, "There are no approved metrics yet.")
    tiles = [{"kind": "metric", "metric": n} for n in dict.fromkeys(names)][:MAX_TILES]
    d = db.scalar(select(m.Dashboard).where(m.Dashboard.tenant_id == p.tenant_id, m.Dashboard.title == "Overview"))
    if d is None:
        d = m.Dashboard(tenant_id=p.tenant_id, title="Overview", tiles=tiles, created_by=p.user_id)
        db.add(d)
    else:
        d.tiles = tiles
    audit(db, p, "dashboard.auto_created", tiles=len(tiles))
    db.commit()
    return _json(d)


def _tile_failed(title: str, kind: str, message: str) -> dict:
    return {"kind": kind, "title": title, "status": "unavailable", "message": message}


def _render_metric(db: Session, p: Principal, name: str) -> dict:
    label = db.scalar(select(m.Metric.label).where(m.Metric.tenant_id == p.tenant_id, m.Metric.name == name).limit(1)) or name
    try:
        total = run_plan(db, p, Plan(intent="metric_total", metric=name, visualization="number"))
        trend = run_plan(db, p, Plan(intent="compare_metric_over_time", metric=name, dimensions=["month"],
                                     visualization="line_chart"))
    except QueryRejected as e:
        return _tile_failed(label, "metric", str(e))
    except Exception:  # noqa: BLE001 - one tile must never break the dashboard
        return _tile_failed(label, "metric", "No approved data model is available for this metric yet.")
    return {"kind": "metric", "title": label, "status": "ok", "visualization": "number+trend",
            "total": total["rows"][0][0] if total["rows"] else None, "trend": trend["rows"],
            "metric": total["metric"], "freshness": total["freshness"], "caveats": total["caveats"][1:]}


def _render_report(db: Session, p: Principal, report_id: str) -> dict:
    r = db.get(m.Report, report_id)
    if r is None or r.tenant_id != p.tenant_id:
        return _tile_failed("Removed report", "report", "This report no longer exists.")
    try:
        if (r.plan or {}).get("intent") == "explain_change":
            e = insights.explain_change(db, p, r.plan["metric"], int(r.plan.get("months", 1)))
            return {"kind": "report", "title": r.title, "status": "ok", "visualization": "text", "headline": e["headline"],
                    "caveats": e["caveats"][:2]}
        res = run_plan(db, p, Plan(**r.plan))
    except (QueryRejected, insights.NotEnoughData) as e:
        return _tile_failed(r.title, "report", str(e))
    except Exception:  # noqa: BLE001
        return _tile_failed(r.title, "report", "This report cannot run right now.")
    return {"kind": "report", "title": r.title, "status": "ok", "visualization": r.plan.get("visualization", "table"),
            "columns": res["columns"], "rows": res["rows"][:200], "metric": res["metric"],
            "freshness": res["freshness"], "caveats": res["caveats"][1:]}


@router.get("/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    d = owned(db, m.Dashboard, dashboard_id, p.tenant_id)
    tiles = [_render_metric(db, p, t["metric"]) if t["kind"] == "metric" else _render_report(db, p, t["report_id"])
             for t in d.tiles]
    return {**_json(d), "rendered": tiles}


@router.delete("/dashboards/{dashboard_id}", status_code=204)
def delete_dashboard(dashboard_id: str, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    d = owned(db, m.Dashboard, dashboard_id, p.tenant_id)
    db.delete(d)
    audit(db, p, "dashboard.deleted", title=d.title)
    db.commit()
