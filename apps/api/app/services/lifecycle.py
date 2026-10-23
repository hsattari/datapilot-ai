"""Data lifecycle: export, erasure and retention (UK GDPR rights; customer off-boarding).

* Export: a ZIP with everything we hold for a tenant - metadata as JSON, analytical tables as CSV, optionally the raw
  uploaded files - plus a manifest with SHA-256 hashes. Password hashes, MFA secrets and recovery codes are never
  included. CSV cells that a spreadsheet would run as a formula are neutralised.
* Tenant deletion: raw objects, the analytical store and every tenant-scoped row are removed. External stores are
  deleted FIRST, database rows last, so a failure part-way leaves the tenant intact and the request can be retried.
  Only a deletion record (ids, counts, time; no names or e-mail) remains.
* User erasure: the person's identifying data is removed; opaque ids remain in audit events.
* Retention: old question history and audit events are purged on a schedule.
"""
import csv
import hashlib
import io
import json
import secrets
import zipfile
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import models as m
from ..config import get_settings
from ..db import Base
from ..security import hash_password
from .backends import get_backend
from .storage import get_storage

EXPORT_TABLE_PREFIXES = ("fact_", "dim_", "quarantine_")  # bronze tables duplicate the raw files
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(v):
    """Neutralise spreadsheet formula injection in text cells (numbers and dates are untouched)."""
    if isinstance(v, str) and v.startswith(_FORMULA_START):
        return "'" + v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def table_csv(cols: list[str], rows: list[tuple]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([csv_cell(v) for v in r])
    return buf.getvalue().encode("utf-8")


def _dump(db: Session, model, tenant_id: str, exclude: tuple[str, ...] = ()) -> list[dict]:
    cols = [c.name for c in model.__table__.columns if c.name not in exclude]
    out = []
    for obj in db.scalars(select(model).where(model.tenant_id == tenant_id)):
        out.append({c: getattr(obj, c) for c in cols})
    return out


def _json(obj) -> bytes:
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False).encode("utf-8")


def build_export(db: Session, tenant_id: str, include_raw: bool = False) -> bytes:
    t = db.get(m.Tenant, tenant_id)
    files: dict[str, bytes] = {}
    files["metadata/tenant.json"] = _json({"id": t.id, "name": t.name, "currency": t.currency,
                                           "timezone": t.timezone, "created_at": t.created_at})
    users = db.execute(select(m.User, m.TenantMembership.role).join(m.TenantMembership, m.TenantMembership.user_id == m.User.id)
                       .where(m.TenantMembership.tenant_id == tenant_id)).all()
    files["metadata/users.json"] = _json([{"id": u.id, "email": u.email, "role": role, "active": not u.disabled,
                                           "mfa_enabled": u.mfa_enabled, "created_at": u.created_at}
                                          for u, role in users])  # no password hash, MFA secret or recovery codes
    for name, model in [("data_sources", m.DataSource), ("source_files", m.SourceFile), ("ingestion_jobs", m.IngestionJob),
                        ("datasets", m.Dataset), ("schema_versions", m.SchemaVersion), ("quality_results", m.QualityResult),
                        ("metrics", m.Metric), ("reports", m.Report), ("alerts", m.Alert),
                        ("questions", m.AnalyticalQuery), ("audit_events", m.AuditEvent)]:
        files[f"metadata/{name}.json"] = _json(_dump(db, model, tenant_id))
    docs = []
    for d in db.scalars(select(m.Document).where(m.Document.tenant_id == tenant_id)):
        chunks = db.scalars(select(m.DocumentChunk.content).where(
            m.DocumentChunk.tenant_id == tenant_id, m.DocumentChunk.document_id == d.id)
            .order_by(m.DocumentChunk.chunk_index)).all()
        docs.append({"id": d.id, "title": d.title, "filename": d.filename, "sha256": d.sha256,
                     "created_at": d.created_at, "text": "\n\n".join(chunks)})  # embeddings are derived data: omitted
    files["metadata/documents.json"] = _json(docs)

    backend = get_backend()
    skipped = []
    for table in sorted(backend.tables(tenant_id)):
        if table.startswith(EXPORT_TABLE_PREFIXES):
            cols, rows = backend.export_table(tenant_id, table)
            files[f"data/{table}.csv"] = table_csv(cols, rows)
        else:
            skipped.append(table)

    if include_raw:
        store = get_storage()
        for f in db.scalars(select(m.SourceFile).where(m.SourceFile.tenant_id == tenant_id)):
            files[f"raw/{f.id}_{f.filename}"] = store.get(f.storage_key)

    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "tenant_id": tenant_id,
                "include_raw": include_raw, "not_included": skipped + ([] if include_raw else ["raw uploaded files"]),
                "files": [{"path": p, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                          for p, b in sorted(files.items())]}
    files["manifest.json"] = _json(manifest)
    files["README.txt"] = (b"DataPilot AI data export. metadata/ holds records as JSON; data/ holds modelled tables as CSV; "
                           b"raw/ (if requested) holds the original uploads. Text cells that begin with = + - @ are prefixed "
                           b"with an apostrophe so spreadsheets do not run them as formulas. manifest.json lists SHA-256 "
                           b"hashes. Passwords, MFA secrets and recovery codes are never exported.\n")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p, b in files.items():
            z.writestr(p, b)
    return out.getvalue()


def delete_tenant(db: Session, tenant_id: str, requested_by: str) -> dict:
    member_ids = list(db.scalars(select(m.TenantMembership.user_id).where(m.TenantMembership.tenant_id == tenant_id)))
    counts: dict[str, int] = {}
    # 1. External stores first: if one fails we raise and nothing in the database has changed (safe to retry).
    counts["raw_files"] = get_storage().delete_prefix(f"tenants/{tenant_id}/")
    counts["analytical_files"] = get_backend().drop_tenant(tenant_id)
    # 2. Database: children before parents, using the foreign-key order of the schema itself.
    for table in reversed(Base.metadata.sorted_tables):
        if "tenant_id" in table.c and table.name != "deletion_log":
            n = db.execute(delete(table).where(table.c.tenant_id == tenant_id)).rowcount or 0
            if n:
                counts[table.name] = n
    db.execute(delete(m.Tenant).where(m.Tenant.id == tenant_id))
    erased = 0
    for uid in member_ids:  # people who belong to no other organisation are erased with it
        if not db.scalar(select(func.count()).select_from(m.TenantMembership).where(m.TenantMembership.user_id == uid)):
            db.execute(delete(m.UserIdentity).where(m.UserIdentity.user_id == uid))
            db.execute(delete(m.User).where(m.User.id == uid))
            erased += 1
    counts["users"] = erased
    db.add(m.DeletionRecord(tenant_id=tenant_id, requested_by=requested_by, counts=counts))
    db.commit()
    return counts


def erase_user(db: Session, tenant_id: str, user_id: str) -> None:
    """Remove a person's identifying data. The row stays (opaque id) so audit events keep a valid reference."""
    u = db.get(m.User, user_id)
    old_email = u.email
    u.email = f"erased-{u.id[:8]}@erased.invalid"
    u.password_hash = hash_password(secrets.token_urlsafe(32))  # unusable
    u.disabled, u.failed_logins, u.locked_until = True, 0, None
    u.mfa_enabled, u.mfa_secret, u.mfa_recovery, u.mfa_last_step = False, None, [], None
    db.execute(delete(m.UserIdentity).where(m.UserIdentity.user_id == user_id))
    db.execute(delete(m.TenantMembership).where(m.TenantMembership.tenant_id == tenant_id,
                                                m.TenantMembership.user_id == user_id))
    for e in db.scalars(select(m.AuditEvent).where(m.AuditEvent.tenant_id == tenant_id)):
        if isinstance(e.detail, dict) and e.detail.get("email") == old_email:
            e.detail = {**e.detail, "email": "[erased]"}


def purge_expired(db: Session, now: datetime | None = None) -> dict:
    s = get_settings()
    now = now or datetime.now(timezone.utc)
    out = {}
    if s.retention_queries_days > 0:
        out["questions"] = db.execute(delete(m.AnalyticalQuery).where(
            m.AnalyticalQuery.created_at < now - timedelta(days=s.retention_queries_days))).rowcount or 0
    if s.retention_audit_days > 0:
        out["audit_events"] = db.execute(delete(m.AuditEvent).where(
            m.AuditEvent.created_at < now - timedelta(days=s.retention_audit_days))).rowcount or 0
    db.commit()
    return out
