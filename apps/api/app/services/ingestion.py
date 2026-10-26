"""Asynchronous ingestion: parse -> profile -> bronze load -> propose mapping.

Permanent errors (bad data) fail immediately; transient errors retry with exponential backoff.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..observability import tracer
from ..models import AuditEvent, Dataset, IngestionJob, SourceFile
from .backends import get_backend
from .modelling import auto_model
from .quality import profile_checks, record_schema_version, store_results
from .profiling import parse_file, profile_frame, suggest_dimension, suggest_mapping
from .storage import get_storage

MAX_ATTEMPTS = 3


def process_job(db: Session, job_id: str) -> IngestionJob:
    with tracer.start_as_current_span("ingestion.job", record_exception=False, set_status_on_exception=False) as span:
        job = _process_job(db, job_id)
        if job is not None:
            span.set_attribute("dp.job_id", job.id)
            span.set_attribute("dp.tenant_id", job.tenant_id)
            span.set_attribute("dp.status", job.status)
            span.set_attribute("dp.attempts", job.attempts)
            span.set_attribute("dp.rows_loaded", job.rows_loaded or 0)
            if job.error_category:
                span.set_attribute("error.category", job.error_category)
        return job


def _process_job(db: Session, job_id: str) -> IngestionJob:
    job = db.get(IngestionJob, job_id)
    if job is None or job.status not in ("queued", "retrying"):
        return job
    job.status, job.attempts = "running", job.attempts + 1
    db.commit()
    s = get_settings()
    try:
        f = db.get(SourceFile, job.file_id)
        assert f.tenant_id == job.tenant_id
        data = get_storage().get(f.storage_key)
        frames = parse_file(f.filename, data, s.max_sheet_rows)
        total = 0
        for name, df in frames.items():
            table = f"bronze_{job.id[:8].replace('-', '')}_{name}"[:60]
            get_backend().load_bronze(job.tenant_id, table, df)
            prof = profile_frame(df)
            sm = suggest_mapping(prof)
            ds = Dataset(tenant_id=job.tenant_id, file_id=f.id, name=name, table_name=table,
                         profile={**prof, "suggested_mapping": sm,
                                  "suggested_dimension": suggest_dimension(prof, bool(sm.get("order_id") or sm.get("net_sales")))})
            db.add(ds)
            db.flush()
            drift = record_schema_version(db, ds, f.source_id, prof)
            store_results(db, job.tenant_id, ds.id, "profile", profile_checks(prof, drift))
            auto_model(db, job.tenant_id, ds)
            total += prof["row_count"]
        job.rows_loaded, job.status = total, "succeeded"
    except (ValueError, KeyError, AssertionError) as e:  # permanent
        job.status, job.error_category, job.error_message = "failed", "data_error", str(e)[:500]
    except Exception as e:  # transient
        job.error_category, job.error_message = "transient", str(e)[:500]
        job.status = "queued" if job.attempts < MAX_ATTEMPTS else "failed"
    job.finished_at = datetime.now(timezone.utc) if job.status in ("succeeded", "failed") else None
    db.add(AuditEvent(tenant_id=job.tenant_id, action=f"ingestion.{job.status}",
                      detail={"job_id": job.id, "attempts": job.attempts}))
    db.commit()
    return job


def run_pending(db: Session) -> int:
    ids = db.scalars(select(IngestionJob.id).where(IngestionJob.status == "queued")).all()
    for i in ids:
        process_job(db, i)
    return len(ids)
