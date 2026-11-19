"""Delete one data source and everything built from it (raw files, bronze table, modelled rows, quality results)."""
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import models as m
from . import modelling
from .backends import get_backend
from .dimensions import DIMS
from .storage import get_storage


class SourceBusy(Exception):
    """A file of this source is still being processed."""


def delete_source(db: Session, tenant_id: str, source_id: str) -> dict:
    """External stores first (raw files, analytical tables); database rows last, in one transaction. If an external
    step fails nothing in the database has changed and the request can be repeated. Does not commit."""
    files = list(db.scalars(select(m.SourceFile).where(m.SourceFile.tenant_id == tenant_id,
                                                       m.SourceFile.source_id == source_id)))
    file_ids = [f.id for f in files]
    jobs = list(db.scalars(select(m.IngestionJob).where(m.IngestionJob.tenant_id == tenant_id,
                                                        m.IngestionJob.file_id.in_(file_ids)))) if file_ids else []
    if any(j.status in ("queued", "running", "retrying") for j in jobs):
        raise SourceBusy()
    datasets = list(db.scalars(select(m.Dataset).where(m.Dataset.tenant_id == tenant_id,
                                                       m.Dataset.file_id.in_(file_ids)))) if file_ids else []
    counts = {"files": len(files), "datasets": len(datasets), "sales_rows_removed": 0}
    be = get_backend()
    roles = set()
    for d in datasets:
        role = d.profile.get("role") if d.model_status == "approved" else None
        r = be.remove_dataset(tenant_id, d.table_name, d.id, role)
        counts["sales_rows_removed"] += r.get("sales_rows_removed", 0)
        if role in DIMS:
            roles.add(role)
    counts["raw_files"] = get_storage().delete_prefix(f"tenants/{tenant_id}/{source_id}/")
    gone = {d.id for d in datasets}
    ds_ids = list(gone)
    if ds_ids:
        db.execute(delete(m.QualityResult).where(m.QualityResult.tenant_id == tenant_id, m.QualityResult.dataset_id.in_(ds_ids)))
        db.execute(delete(m.SchemaVersion).where(m.SchemaVersion.tenant_id == tenant_id, m.SchemaVersion.dataset_id.in_(ds_ids)))
        db.execute(delete(m.Dataset).where(m.Dataset.tenant_id == tenant_id, m.Dataset.id.in_(ds_ids)))
    if file_ids:
        db.execute(delete(m.IngestionJob).where(m.IngestionJob.tenant_id == tenant_id, m.IngestionJob.file_id.in_(file_ids)))
        db.execute(delete(m.SourceFile).where(m.SourceFile.tenant_id == tenant_id, m.SourceFile.id.in_(file_ids)))
    db.execute(delete(m.DataSource).where(m.DataSource.tenant_id == tenant_id, m.DataSource.id == source_id))
    db.flush()
    # A product/customer list that was removed: rebuild from another approved list of the same kind, if any remains.
    for role in roles:
        other = modelling.approved_dimensions(db, tenant_id).get(role)
        if other is not None:
            be.build_dimension(tenant_id, other.table_name, role, other.column_mapping)
    modelling.refresh_relationships(db, tenant_id)
    return counts
