"""Workspace features: saved reports, metric alerts, activity log, jobs, usage and settings.

Every query is scoped by the caller's tenant (resolved server-side); IDs from other tenants return 404.
"""
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from ..db import get_db
from ..deps import Principal, get_principal, require_admin, require_writer
from ..services import insights
from ..services.alerts import evaluate_alert
from ..services.common import audit, owned, run_plan
from ..services.planner import make_plan
from ..services.query_engine import Plan, QueryRejected

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------- reports
class ReportIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=1000)


@router.post("/reports", status_code=201)
def create_report(body: ReportIn, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    approved = {x.name: x.description for x in db.scalars(select(m.Metric).where(
        m.Metric.tenant_id == p.tenant_id, m.Metric.status == "approved"))}
    unapproved = {x.name: x.description for x in db.scalars(select(m.Metric).where(
        m.Metric.tenant_id == p.tenant_id, m.Metric.status != "approved"))}
    res = make_plan(body.question, approved, unapproved)
    if res.outcome == "explain":   # a "why did it change?" report: stored as a small spec, recomputed on every run
        try:
            insights.explain_change(db, p, res.metric, res.months)
        except insights.NotEnoughData as e:
            raise HTTPException(422, str(e))
        except QueryRejected as e:
            raise HTTPException(422, str(e))
        except Exception:
            raise HTTPException(422, "No approved data model is available yet. Upload and approve a dataset first.")
        r = m.Report(tenant_id=p.tenant_id, title=body.title, question=body.question,
                     plan={"intent": "explain_change", "metric": res.metric, "months": res.months}, created_by=p.user_id)
        db.add(r)
        audit(db, p, "report.created", title=body.title)
        db.commit()
        return {"id": r.id, "title": r.title}
    if res.outcome != "plan":
        raise HTTPException(422, res.message or "That question cannot be saved as a report")
    try:
        run_plan(db, p, res.plan)  # only questions that run today can be saved
    except QueryRejected as e:
        raise HTTPException(422, str(e))
    except Exception:
        raise HTTPException(422, "No approved data model is available yet. Upload and approve a dataset first.")
    r = m.Report(tenant_id=p.tenant_id, title=body.title, question=body.question, plan=res.plan.model_dump(),
                 created_by=p.user_id)
    db.add(r)
    audit(db, p, "report.created", title=body.title)
    db.commit()
    return {"id": r.id, "title": r.title}


@router.get("/reports")
def list_reports(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(m.Report).where(m.Report.tenant_id == p.tenant_id).order_by(m.Report.created_at.desc()))
    return [{"id": r.id, "title": r.title, "question": r.question, "created_at": r.created_at.isoformat()} for r in rows]


@router.get("/reports/{report_id}/run")
def run_report(report_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    r = owned(db, m.Report, report_id, p.tenant_id)
    if r.plan.get("intent") == "explain_change":
        try:   # metric approval is re-checked inside, exactly as for any other report
            out = insights.explain_change(db, p, r.plan["metric"], int(r.plan.get("months", 1)))
        except insights.NotEnoughData as e:
            return {"outcome": "clarification", "message": str(e), "title": r.title}
        except QueryRejected as e:
            return {"outcome": "refusal", "message": str(e), "title": r.title}
        except Exception:
            return {"outcome": "clarification", "title": r.title,
                    "message": "No approved data model is available for this report right now."}
        audit(db, p, "report.run", report_id=r.id)
        db.commit()
        return {"outcome": "explanation", "title": r.title, **out}
    try:
        out = run_plan(db, p, Plan(**r.plan))  # metric approval and dimensions are re-checked on every run
    except QueryRejected as e:
        return {"outcome": "refusal", "message": str(e), "title": r.title}
    except Exception:
        return {"outcome": "clarification", "title": r.title,
                "message": "No approved data model is available for this report right now."}
    audit(db, p, "report.run", report_id=r.id)
    db.commit()
    return {"outcome": "answered", "title": r.title, "plan": r.plan, **out}


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(report_id: str, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    r = owned(db, m.Report, report_id, p.tenant_id)
    db.delete(r)
    audit(db, p, "report.deleted", title=r.title)
    db.commit()


# ---------------------------------------------------------------- alerts
class AlertIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    metric: str = Field(min_length=1, max_length=100)
    comparison: Literal["above", "below"]
    threshold: float


def _alert_json(a: m.Alert) -> dict:
    return {"id": a.id, "name": a.name, "metric": a.metric, "comparison": a.comparison, "threshold": a.threshold,
            "last_value": a.last_value, "last_state": a.last_state,
            "last_checked_at": a.last_checked_at.isoformat() if a.last_checked_at else None}


@router.post("/alerts", status_code=201)
def create_alert(body: AlertIn, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    metric = db.scalar(select(m.Metric).where(m.Metric.tenant_id == p.tenant_id, m.Metric.name == body.metric,
                                              m.Metric.status == "approved"))
    if metric is None:
        raise HTTPException(422, "Alerts can only use approved metrics")
    a = m.Alert(tenant_id=p.tenant_id, name=body.name, metric=body.metric, comparison=body.comparison,
                threshold=body.threshold, created_by=p.user_id)
    db.add(a)
    audit(db, p, "alert.created", name=body.name, metric=body.metric)
    db.commit()
    return _alert_json(a)


@router.get("/alerts")
def list_alerts(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return [_alert_json(a) for a in db.scalars(select(m.Alert).where(m.Alert.tenant_id == p.tenant_id)
                                               .order_by(m.Alert.created_at))]


@router.post("/alerts/evaluate")
def evaluate_alerts(p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    """Check every alert against current data now (the worker also does this on a schedule)."""
    out = []
    for a in db.scalars(select(m.Alert).where(m.Alert.tenant_id == p.tenant_id).order_by(m.Alert.created_at)):
        evaluate_alert(db, p, a)
        if a.last_state == "triggered":
            audit(db, p, "alert.triggered", alert_id=a.id, value=a.last_value)
        out.append(_alert_json(a))
    db.commit()
    return {"alerts": out}


@router.delete("/alerts/{alert_id}", status_code=204)
def delete_alert(alert_id: str, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    a = owned(db, m.Alert, alert_id, p.tenant_id)
    db.delete(a)
    audit(db, p, "alert.deleted", name=a.name)
    db.commit()


# ------------------------------------------------- activity, jobs, usage
@router.get("/activity")
def activity(limit: int = 50, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    """Audit trail for the workspace (admins only)."""
    limit = max(1, min(limit, 200))
    rows = db.scalars(select(m.AuditEvent).where(m.AuditEvent.tenant_id == p.tenant_id)
                      .order_by(m.AuditEvent.created_at.desc()).limit(limit))
    return [{"id": e.id, "action": e.action, "user_id": e.user_id, "detail": e.detail,
             "created_at": e.created_at.isoformat()} for e in rows]


@router.get("/jobs")
def list_jobs(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(select(m.IngestionJob, m.SourceFile.filename).join(m.SourceFile, m.SourceFile.id == m.IngestionJob.file_id)
                      .where(m.IngestionJob.tenant_id == p.tenant_id).order_by(m.IngestionJob.created_at.desc()).limit(50)).all()
    return [{"id": j.id, "filename": fn, "status": j.status, "attempts": j.attempts, "rows_loaded": j.rows_loaded,
             "error_category": j.error_category, "error_message": j.error_message,
             "created_at": j.created_at.isoformat()} for j, fn in rows]


@router.get("/usage")
def usage(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    t = p.tenant_id
    since = datetime.now(timezone.utc) - timedelta(days=30)

    def count(model, *where):
        return db.scalar(select(func.count()).select_from(model).where(model.tenant_id == t, *where)) or 0

    q30 = {o: db.scalar(select(func.count()).select_from(m.AnalyticalQuery).where(
        m.AnalyticalQuery.tenant_id == t, m.AnalyticalQuery.created_at >= since, m.AnalyticalQuery.outcome == o)) or 0
        for o in ("answered", "refusal", "clarification")}
    return {"files": count(m.SourceFile), "datasets": count(m.Dataset), "documents": count(m.Document),
            "reports": count(m.Report), "alerts": count(m.Alert),
            "rows_loaded": db.scalar(select(func.coalesce(func.sum(m.IngestionJob.rows_loaded), 0)).where(
                m.IngestionJob.tenant_id == t)) or 0,
            "questions_last_30_days": q30}


# ------------------------------------------------------------- settings
class SettingsIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    timezone: str | None = Field(default=None, max_length=64)
    auto_model: bool | None = None
    require_mfa: bool | None = None


@router.get("/settings")
def get_settings_(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    t = db.get(m.Tenant, p.tenant_id)
    return {"name": t.name, "currency": t.currency, "timezone": t.timezone, "auto_model": t.auto_model,
            "require_mfa": t.require_mfa, "your_role": p.role}


@router.patch("/settings")
def update_settings(body: SettingsIn, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    t = db.get(m.Tenant, p.tenant_id)
    if body.timezone is not None:
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(422, "Unknown time zone")
        t.timezone = body.timezone
    if body.name is not None:
        t.name = body.name
    if body.currency is not None:
        t.currency = body.currency
    if body.auto_model is not None:
        t.auto_model = body.auto_model
    if body.require_mfa is not None:
        if body.require_mfa:
            me_ = db.get(m.User, p.user_id)
            sso = db.scalar(select(m.UserIdentity.id).where(m.UserIdentity.user_id == p.user_id))
            if not me_.mfa_enabled and not sso:
                raise HTTPException(422, "Turn on two-step sign-in for your own account first, so you are not locked out")
        t.require_mfa = body.require_mfa
    audit(db, p, "settings.updated", **body.model_dump(exclude_none=True))
    db.commit()
    return {"name": t.name, "currency": t.currency, "timezone": t.timezone, "auto_model": t.auto_model,
            "require_mfa": t.require_mfa}
