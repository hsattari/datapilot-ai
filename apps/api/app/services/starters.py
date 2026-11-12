"""Starter content, created once when an organisation's first sales data is modelled automatically.

Without it the Dashboards, Reports and summary pages are empty after a successful upload and the person has to know what
to click. Everything created here is ordinary content (an Overview dashboard, saved reports, a management summary) that
goes through the same governed engine as anything a person creates, and can be deleted. It is created once per
organisation (recorded in the audit log), so deleting it is never undone by a later upload. Nothing here may fail an upload.
"""
import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from . import summaries
from .common import run_plan
from .planner import make_plan
from .query_engine import QueryRejected

log = logging.getLogger("datapilot.starters")
QUESTIONS = [("Net revenue by month", "Net revenue by month"),
             ("Net revenue by product", "Net revenue by product"),
             ("Net revenue by region", "Net revenue by region"),
             ("Why did net revenue change?", "Why did net revenue change?")]
MAX_TILES = 12


def create_once(db: Session, tenant_id: str) -> dict | None:
    """Returns what was created, or None if starters already existed or there is nothing to build them from."""
    if db.scalar(select(m.AuditEvent.id).where(m.AuditEvent.tenant_id == tenant_id,
                                               m.AuditEvent.action == "starters.created").limit(1)):
        return None
    p = summaries._admin(db, tenant_id)
    names = [x.name for x in db.scalars(select(m.Metric).where(m.Metric.tenant_id == tenant_id, m.Metric.status == "approved")
                                        .order_by(m.Metric.name))]
    if p is None or not names:
        return None
    approved = {x.name: x.description for x in db.scalars(select(m.Metric).where(m.Metric.tenant_id == tenant_id, m.Metric.status == "approved"))}
    unapproved = {x.name: x.description for x in db.scalars(select(m.Metric).where(m.Metric.tenant_id == tenant_id, m.Metric.status != "approved"))}
    made_reports, skipped = [], []
    for title, question in QUESTIONS:
        try:
            with db.begin_nested():
                res = make_plan(question, approved, unapproved)
                if res.outcome == "plan":
                    run_plan(db, p, res.plan)           # only what runs today is saved
                    spec = res.plan.model_dump()
                elif res.outcome == "explain":
                    spec = {"intent": "explain_change", "metric": res.metric, "months": res.months}
                else:
                    raise QueryRejected(res.message or "not available")
                r = m.Report(tenant_id=tenant_id, title=title, question=question, plan=spec, created_by=p.user_id)
                db.add(r)
                db.flush()
                made_reports.append(r)
        except Exception as e:  # noqa: BLE001 - e.g. no region column, or too little history for "why"
            skipped.append({"title": title, "reason": str(e)[:120]})
    tiles = [{"kind": "metric", "metric": n} for n in names][:MAX_TILES - len(made_reports)]
    tiles += [{"kind": "report", "report_id": r.id} for r in made_reports if r.plan.get("intent") != "explain_change"]
    db.add(m.Dashboard(tenant_id=tenant_id, title="Overview", tiles=tiles[:MAX_TILES], created_by=p.user_id))
    end = date.today()
    summary = None
    try:
        with db.begin_nested():
            summary = summaries.upsert(db, tenant_id, "on_demand", end - timedelta(days=6), end)
    except Exception as e:  # noqa: BLE001
        skipped.append({"title": "Management summary", "reason": str(e)[:120]})
    out = {"reports": [r.title for r in made_reports], "dashboard": "Overview", "summary": summary is not None, "skipped": skipped}
    db.add(m.AuditEvent(tenant_id=tenant_id, user_id=None, action="starters.created", detail=out))
    log.info("starter content created for %s: %s", tenant_id, out)
    return out
