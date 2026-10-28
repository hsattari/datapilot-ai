"""Management summaries: what moved, what happened and what needs attention, from approved metrics and real records.

Deterministic: the numbers come from the same governed query path as every answer (insights.overview) and from the
platform's own records. No model writes any figure. Metric movements are month-on-month because that is the grain
the query engine supports; the weekly part is the delivery cadence and the operational events of that week.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..deps import Principal
from . import health, insights

NOTE = ("Metric movements compare the latest complete month with the one before it. Operational items cover the dates "
        "shown. This summary describes where numbers moved; it does not explain why.")


def _admin(db: Session, tenant_id: str) -> Principal | None:
    mem = db.scalar(select(m.TenantMembership).where(m.TenantMembership.tenant_id == tenant_id,
                                                      m.TenantMembership.role == "admin").limit(1))
    return Principal(user_id=mem.user_id, tenant_id=tenant_id, role="analyst") if mem else None


def _in(dt: datetime, start: date, end: date) -> bool:
    d = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).date()
    return start <= d <= end


def build(db: Session, tenant_id: str, start: date, end: date, now: datetime | None = None) -> dict | None:
    """Content for the period, or None if the organisation has nothing to report yet (no approved metric)."""
    p = _admin(db, tenant_id)
    names = [x.name for x in db.scalars(select(m.Metric).where(m.Metric.tenant_id == tenant_id, m.Metric.status == "approved"))]
    has_data = db.scalar(select(m.Dataset.id).where(m.Dataset.tenant_id == tenant_id,
                                                    m.Dataset.model_status == "approved").limit(1)) is not None
    if p is None or not names or not has_data:
        return None   # nothing to report until a model is built from uploaded data
    metrics_ = insights.overview(db, p, sorted(set(names)))

    files = [f for f in db.scalars(select(m.SourceFile).where(m.SourceFile.tenant_id == tenant_id)) if _in(f.created_at, start, end)]
    fids = {f.id for f in files}
    jobs = [j for j in db.scalars(select(m.IngestionJob).where(m.IngestionJob.tenant_id == tenant_id)) if j.file_id in fids]
    events = [e for e in db.scalars(select(m.AuditEvent).where(m.AuditEvent.tenant_id == tenant_id)) if _in(e.created_at, start, end)]
    count = lambda action: sum(1 for e in events if e.action == action)  # noqa: E731
    qs = [q for q in db.scalars(select(m.AnalyticalQuery).where(m.AnalyticalQuery.tenant_id == tenant_id)) if _in(q.created_at, start, end)]
    attention = health.issues(db, tenant_id, now)
    triggered_now = [a for a in db.scalars(select(m.Alert).where(m.Alert.tenant_id == tenant_id)) if a.last_state == "triggered"]

    ops = {"files_uploaded": len(files), "uploads_failed": sum(1 for j in jobs if j.status == "failed"),
           "rows_loaded": sum(j.rows_loaded or 0 for j in jobs if j.status == "succeeded"),
           "alerts_triggered": count("alert.triggered"), "alerts_recovered": count("alert.recovered"),
           "questions_answered": sum(1 for q in qs if q.outcome == "answered"),
           "questions_refused": sum(1 for q in qs if q.outcome == "refusal")}
    bits = []
    for x in metrics_:
        t = x["total"]
        if t["percent"] is not None:
            bits.append(f"{x['label']} {'up' if t['change'] > 0 else 'down' if t['change'] < 0 else 'flat'} {abs(t['percent']):.1f}%")
    lead = ("; ".join(bits) + ".") if bits else "Not enough complete months yet to compare metrics."
    tail = []
    if attention:
        tail.append(f"{len(attention)} items need attention" if len(attention) != 1 else "1 item needs attention")
    if triggered_now:
        tail.append(f"{len(triggered_now)} alert{'s are' if len(triggered_now) != 1 else ' is'} triggered")
    headline = lead + (" " + "; ".join(tail).capitalize() + "." if tail else " Nothing needs attention.")
    caveats = []
    for x in metrics_:
        for c in x["caveats"]:
            if c not in caveats:
                caveats.append(c)
    return {"headline": headline, "period": {"start": start.isoformat(), "end": end.isoformat()},
            "metrics": [{"label": x["label"], "headline": x["headline"], "total": x["total"],
                         "top_driver": x["top_driver"], "anomaly": x["anomaly"]} for x in metrics_],
            "operations": ops, "attention": attention,
            "alerts_triggered_now": [{"name": a.name, "metric": a.metric, "value": a.last_value} for a in triggered_now],
            "caveats": caveats[:6], "note": NOTE}


def upsert(db: Session, tenant_id: str, kind: str, start: date, end: date, now: datetime | None = None) -> m.Summary | None:
    content = build(db, tenant_id, start, end, now)
    if content is None:
        return None
    row = db.scalar(select(m.Summary).where(m.Summary.tenant_id == tenant_id, m.Summary.kind == kind,
                                            m.Summary.period_start == start.isoformat()))
    if row is None:
        row = m.Summary(tenant_id=tenant_id, kind=kind, period_start=start.isoformat(), period_end=end.isoformat(), content=content)
        db.add(row)
    else:
        row.period_end, row.content, row.created_at = end.isoformat(), content, datetime.now(timezone.utc)
    db.flush()
    return row


def run_weekly(db: Session, now: datetime | None = None) -> int:
    """Worker: create last week's summary (Monday to Sunday) for every organisation that has none yet."""
    now = now or datetime.now(timezone.utc)
    this_monday = now.date() - timedelta(days=now.date().weekday())
    start = this_monday - timedelta(days=7)
    made = 0
    for tid in db.scalars(select(m.Tenant.id)).all():
        exists = db.scalar(select(m.Summary.id).where(m.Summary.tenant_id == tid, m.Summary.kind == "weekly",
                                                       m.Summary.period_start == start.isoformat()))
        if exists:
            continue
        try:
            if upsert(db, tid, "weekly", start, start + timedelta(days=6), now) is not None:
                made += 1
        except Exception:  # noqa: BLE001 - one organisation's failure must not stop the others
            db.rollback()
    db.commit()
    return made
