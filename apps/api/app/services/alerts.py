"""Alert evaluation, shared by the on-demand endpoint and the scheduled run in the worker.

The scheduled run records an event only when an alert CHANGES state (ok -> triggered, triggered -> ok), so a
metric that stays above its threshold does not write a new log entry every hour.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..deps import Principal
from .common import run_plan
from .query_engine import Plan


def evaluate_alert(db: Session, p: Principal, a: m.Alert) -> str | None:
    """Update the alert's state from current data. Returns the previous state."""
    prev = a.last_state
    try:
        res = run_plan(db, p, Plan(intent="metric_total", metric=a.metric, visualization="number"))
        val = res["rows"][0][0] if res["rows"] else None
        if val is None:
            a.last_state, a.last_value = "error", None
        else:
            a.last_value = float(val)
            hit = a.last_value > a.threshold if a.comparison == "above" else a.last_value < a.threshold
            a.last_state = "triggered" if hit else "ok"
    except Exception:  # noqa: BLE001 - one broken alert must not stop the others
        a.last_state, a.last_value = "error", None
    a.last_checked_at = datetime.now(timezone.utc)
    return prev


def run_due_alerts(db: Session, interval_minutes: int, now: datetime | None = None) -> int:
    """Evaluate every alert (all organisations) not checked within the interval. Returns how many were evaluated."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=interval_minutes)
    n = 0
    for a in db.scalars(select(m.Alert).order_by(m.Alert.created_at)).all():
        checked = a.last_checked_at
        if checked is not None and checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        if checked is not None and checked > cutoff:
            continue
        p = Principal(user_id=a.created_by, tenant_id=a.tenant_id, role="analyst")
        prev = evaluate_alert(db, p, a)
        n += 1
        if a.last_state == "triggered" and prev != "triggered":
            action = "alert.triggered"
        elif a.last_state == "ok" and prev == "triggered":
            action = "alert.recovered"
        else:
            action = None
        if action:
            db.add(m.AuditEvent(tenant_id=a.tenant_id, action=action,
                                detail={"alert_id": a.id, "name": a.name, "value": a.last_value, "scheduled": True}))
        db.commit()
    return n
