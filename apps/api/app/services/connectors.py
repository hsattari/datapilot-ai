"""URL connector: fetch a file from a web address on demand or on a schedule and hand it to the normal intake.

Only public https addresses (see netguard). The address may be a pre-signed link; it is shown to administrators and
writers of the organisation only, and audit logs record the host and path, never the query string (which may hold a
token). Credentials are deliberately not supported here: a per-organisation secret store is a separate decision.
"""
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..config import get_settings
from . import malware, netguard, quotas
from .storage import get_storage, safe_filename
from .validation import ALLOWED, UploadRejected, validate_upload

TYPES = {"text/csv": ".csv", "application/json": ".json", "application/x-ndjson": ".jsonl", "text/plain": ".txt",
         "application/xml": ".xml", "text/xml": ".xml",
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx"}


class SyncFailed(Exception):
    """Safe to show to the person."""


def redact(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.hostname}{u.path}"[:300]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _filename(source: m.DataSource, final_url: str, ctype: str) -> str:
    from pathlib import PurePosixPath
    base = PurePosixPath(urlsplit(final_url).path).name
    if base and PurePosixPath(base).suffix.lower() in ALLOWED:
        return safe_filename(base)
    ext = TYPES.get(ctype)
    if ext is None:
        raise SyncFailed(f"The server sent '{ctype or 'unknown'}' and the address has no file extension; "
                         f"use an address ending in one of {sorted(ALLOWED)}.")
    return safe_filename(f"{source.name}{ext}")


def _event(db: Session, source: m.DataSource, action: str, **detail) -> None:
    db.add(m.AuditEvent(tenant_id=source.tenant_id, user_id=None, action=action,
                        detail={"source_id": source.id, "address": redact(source.url or ""), **detail}))


def sync(db: Session, source: m.DataSource, *, transport: httpx.BaseTransport | None = None) -> dict:
    """Fetch now. Returns {status: 'queued'|'unchanged', ...}; raises SyncFailed. Always records the outcome."""
    cfg = get_settings()
    try:
        if not source.url:
            raise SyncFailed("This data source has no web address.")
        quotas.enforce(db, source.tenant_id, "uploads")
        quotas.enforce(db, source.tenant_id, "rows")
        try:
            got = netguard.fetch(source.url, max_bytes=cfg.max_upload_mb * 1024 * 1024,
                                 timeout=cfg.connector_timeout_seconds, allow_http=cfg.connector_allow_http,
                                 transport=transport)
        except netguard.UrlRejected as e:
            raise SyncFailed(str(e)) from None
        name = _filename(source, got.final_url, got.content_type)
        try:
            validate_upload(name, got.data, cfg.max_upload_mb)
        except UploadRejected as e:
            raise SyncFailed(str(e)) from None
        if malware.enabled():
            try:
                verdict = malware.scan_bytes(got.data)
            except malware.ScannerUnavailable:
                if not cfg.malware_scan_fail_open:
                    raise SyncFailed("File scanning is temporarily unavailable; nothing was stored.") from None
            else:
                if verdict.status == "infected":
                    raise SyncFailed("The file was rejected by the malware scan and was not stored.")
        sha = hashlib.sha256(got.data).hexdigest()
        last = db.scalar(select(m.SourceFile).where(m.SourceFile.tenant_id == source.tenant_id,
                                                    m.SourceFile.source_id == source.id)
                         .order_by(m.SourceFile.created_at.desc()))
        source.last_sync_at, source.last_sync_error = _now(), None
        if last is not None and last.sha256 == sha:
            _event(db, source, "source.sync_unchanged", sha256=sha)
            db.commit()
            return {"status": "unchanged", "sha256": sha, "file_id": last.id}
        version = 1 + (db.scalar(select(m.SourceFile.version).where(
            m.SourceFile.tenant_id == source.tenant_id, m.SourceFile.source_id == source.id,
            m.SourceFile.filename == name).order_by(m.SourceFile.version.desc())) or 0)
        sf = m.SourceFile(tenant_id=source.tenant_id, source_id=source.id, filename=name, sha256=sha,
                          size_bytes=len(got.data), storage_key="", version=version)
        db.add(sf)
        db.flush()
        sf.storage_key = f"tenants/{source.tenant_id}/{source.id}/{sf.id}/{name}"
        get_storage().put(sf.storage_key, got.data)
        job = m.IngestionJob(tenant_id=source.tenant_id, file_id=sf.id)
        db.add(job)
        _event(db, source, "source.sync_accepted", file_id=sf.id, sha256=sha, bytes=len(got.data))
        db.commit()
        return {"status": "queued", "file_id": sf.id, "job_id": job.id, "sha256": sha, "version": version}
    except SyncFailed as e:
        db.rollback()
        source = db.get(m.DataSource, source.id)
        source.last_sync_at, source.last_sync_error = _now(), str(e)[:500]
        _event(db, source, "source.sync_failed", reason=str(e)[:300])
        db.commit()
        raise


def run_due(db: Session, now: datetime | None = None) -> int:
    """Worker: sync every source whose schedule is due. One failing source never stops the others."""
    now = now or _now()
    n = 0
    for s in db.scalars(select(m.DataSource).where(m.DataSource.url.is_not(None), m.DataSource.sync_every_hours.is_not(None))).all():
        last = s.last_sync_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is not None and now - last < timedelta(hours=s.sync_every_hours):
            continue
        try:
            sync(db, s)
            n += 1
        except SyncFailed:
            n += 1
        except Exception:  # noqa: BLE001
            db.rollback()
    return n
