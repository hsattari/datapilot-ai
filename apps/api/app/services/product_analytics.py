"""Product analytics for one organisation: activation, time to first dashboard, retention, cost to serve.

Everything is counted from records the app already keeps (uploads, models, dashboards, questions, the audit trail).
No tracking, no page views, no content. Shown to administrators; scripts/product_report.py aggregates across
organisations for the service owner."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from .. import models as m
from . import aibudget


def _first(db: Session, col, *where):
    return db.scalar(select(func.min(col)).where(*where))


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def _minutes(a, b):
    a, b = _aware(a), _aware(b)
    return None if a is None or b is None else round(max((b - a).total_seconds(), 0) / 60, 1)


def active_users(db: Session, tenant_id: str, since: datetime, until: datetime | None = None) -> int:
    q = select(func.count(distinct(m.AuditEvent.user_id))).where(
        m.AuditEvent.tenant_id == tenant_id, m.AuditEvent.user_id.is_not(None), m.AuditEvent.created_at >= since)
    if until is not None:
        q = q.where(m.AuditEvent.created_at < until)
    return int(db.scalar(q) or 0)


def tenant_metrics(db: Session, tenant_id: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    t = db.get(m.Tenant, tenant_id)
    first_upload = _first(db, m.SourceFile.created_at, m.SourceFile.tenant_id == tenant_id)
    first_model = _first(db, m.Dataset.created_at, m.Dataset.tenant_id == tenant_id, m.Dataset.model_status == "approved")
    first_dash = _first(db, m.Dashboard.created_at, m.Dashboard.tenant_id == tenant_id)
    first_answer = _first(db, m.AnalyticalQuery.created_at, m.AnalyticalQuery.tenant_id == tenant_id,
                          m.AnalyticalQuery.outcome == "answered")
    steps = {"uploaded_a_file": first_upload is not None, "has_an_approved_model": first_model is not None,
             "has_a_dashboard": first_dash is not None, "asked_a_question": first_answer is not None}
    weeks = []
    for i in range(7, -1, -1):   # last 8 weeks, oldest first: distinct people who did anything that week
        end = now - timedelta(days=7 * i)
        weeks.append({"week_ending": end.date().isoformat(), "active_people": active_users(db, tenant_id, end - timedelta(days=7), end)})
    questions_30d = int(db.scalar(select(func.count()).select_from(m.AnalyticalQuery).where(
        m.AnalyticalQuery.tenant_id == tenant_id, m.AnalyticalQuery.created_at >= now - timedelta(days=30))) or 0)
    rows_30d = int(db.scalar(select(func.coalesce(func.sum(m.IngestionJob.rows_loaded), 0)).where(
        m.IngestionJob.tenant_id == tenant_id, m.IngestionJob.created_at >= now - timedelta(days=30))) or 0)
    return {
        "organisation_created": _aware(t.created_at).isoformat() if t else None,
        "activated": all(steps.values()), "activation_steps": steps,
        "minutes_to_first_upload": _minutes(t.created_at if t else None, first_upload),
        "minutes_to_first_dashboard": _minutes(t.created_at if t else None, first_dash),
        "minutes_to_first_answer": _minutes(t.created_at if t else None, first_answer),
        "active_people_7d": active_users(db, tenant_id, now - timedelta(days=7)),
        "active_people_30d": active_users(db, tenant_id, now - timedelta(days=30)),
        "weekly_active_people": weeks,
        "cost_to_serve_30d": {"questions": questions_30d, "rows_loaded": rows_30d,
                              "ai_tokens_this_month": aibudget.used(db, tenant_id)},
    }
