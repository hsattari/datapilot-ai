"""Data-source health: failed refreshes, changed schemas and files that did not arrive.

Computed from records the platform already keeps, so it can be checked on demand and by the worker. A failed refresh
counts only while it is the latest attempt for that source (a later good upload resolves it).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m

SCHEMA_WINDOW_DAYS = 14


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _latest_jobs(db: Session, tenant_id: str) -> dict[str, tuple[m.SourceFile, m.IngestionJob | None]]:
    """Latest uploaded file per source with its ingestion job."""
    out: dict[str, tuple[m.SourceFile, m.IngestionJob | None]] = {}
    for f in db.scalars(select(m.SourceFile).where(m.SourceFile.tenant_id == tenant_id).order_by(m.SourceFile.created_at)):
        j = db.scalar(select(m.IngestionJob).where(m.IngestionJob.tenant_id == tenant_id, m.IngestionJob.file_id == f.id))
        out[f.source_id] = (f, j)   # ascending order: the last one wins
    return out


def sources(db: Session, tenant_id: str, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    latest = _latest_jobs(db, tenant_id)
    rows = []
    for s in db.scalars(select(m.DataSource).where(m.DataSource.tenant_id == tenant_id).order_by(m.DataSource.created_at)):
        f, j = latest.get(s.id, (None, None))
        last_at = _aware(f.created_at) if f else None
        state, message = "ok", "Up to date"
        if s.last_sync_error and (f is None or s.last_sync_at is None or _aware(s.last_sync_at) >= _aware(f.created_at)):
            state, message = "failed", f"The last fetch from the web address failed: {s.last_sync_error}"
        elif f is None:
            state, message = ("late", "Nothing has been uploaded yet") if s.expected_every_days else ("empty", "No files yet")
        elif j is not None and j.status == "failed":
            state, message = "failed", f"The latest upload ({f.filename}) failed: {j.error_message or 'unknown error'}"
        elif j is not None and j.status in ("queued", "running", "retrying"):
            state, message = "processing", f"{f.filename} is being processed"
        if state in ("ok", "empty") and s.expected_every_days and last_at is not None:
            age = (now - last_at).days
            if now - last_at > timedelta(days=s.expected_every_days):
                state = "late"
                message = f"No new file for {age} days; one was expected every {s.expected_every_days}"
        rows.append({"id": s.id, "name": s.name, "state": state, "message": message,
                     "expected_every_days": s.expected_every_days,
                     "last_upload_at": last_at.isoformat() if last_at else None,
                     "last_file": f.filename if f else None,
                     "connected": bool(s.url), "sync_every_hours": s.sync_every_hours})
    return rows


def issues(db: Session, tenant_id: str, now: datetime | None = None) -> list[dict]:
    """Things that need attention. Each has a stable `key` so the worker records a new one only once."""
    now = now or datetime.now(timezone.utc)
    out: list[dict] = []
    for s in sources(db, tenant_id, now):
        if s["state"] == "failed":
            out.append({"kind": "refresh_failed", "severity": "high", "key": f"failed:{s['id']}:{s['last_upload_at']}",
                        "source": s["name"], "message": f"{s['name']}: {s['message']}"})
        elif s["state"] == "late":
            out.append({"kind": "file_missing", "severity": "medium", "key": f"late:{s['id']}:{(s['last_upload_at'] or '')[:10]}",
                        "source": s["name"], "message": f"{s['name']}: {s['message']}"})
    since = now - timedelta(days=SCHEMA_WINDOW_DAYS)
    for v in db.scalars(select(m.SchemaVersion).where(m.SchemaVersion.tenant_id == tenant_id).order_by(m.SchemaVersion.created_at)):
        ch = v.changes or {}
        if v.version < 2 or _aware(v.created_at) < since or not any(ch.get(k) for k in ("added", "removed", "type_changed")):
            continue
        parts = []
        if ch.get("removed"):
            parts.append("columns removed: " + ", ".join(ch["removed"]))
        if ch.get("type_changed"):
            parts.append("type changed: " + ", ".join(ch["type_changed"]))
        if ch.get("added"):
            parts.append("columns added: " + ", ".join(ch["added"]))
        harmful = bool(ch.get("removed") or ch.get("type_changed"))
        out.append({"kind": "schema_changed", "severity": "medium" if harmful else "low", "key": f"schema:{v.id}",
                    "source": v.dataset_name,
                    "message": f"{v.dataset_name} (version {v.version}): " + "; ".join(parts)
                               + (". Check that the column mapping still points at the right columns." if harmful else ".")})
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda i: order[i["severity"]])


def record_new_issues(db: Session, now: datetime | None = None) -> int:
    """Worker: write one activity event per new issue (all organisations). Returns how many were recorded."""
    now = now or datetime.now(timezone.utc)
    n = 0
    for tid in db.scalars(select(m.Tenant.id)).all():
        seen = {(e.detail or {}).get("key") for e in db.scalars(select(m.AuditEvent).where(
            m.AuditEvent.tenant_id == tid, m.AuditEvent.action == "health.issue",
            m.AuditEvent.created_at >= now - timedelta(days=30)))}
        for i in issues(db, tid, now):
            if i["key"] in seen:
                continue
            db.add(m.AuditEvent(tenant_id=tid, action="health.issue",
                                detail={"key": i["key"], "kind": i["kind"], "severity": i["severity"], "message": i["message"]}))
            n += 1
    db.commit()
    return n
