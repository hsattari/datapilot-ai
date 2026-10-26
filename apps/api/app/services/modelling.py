"""Building a dataset's model, shared by the approval endpoint and automatic modelling after upload.

Automatic modelling only acts when the detection is unambiguous (required fields found, with the right data
types, nothing flagged for clarification). Anything else stays "proposed" for a person to confirm. Every
automatic decision is written to the activity log so it can be reviewed, and an administrator can turn the
feature off per organisation (Settings).
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from .backends import get_backend
from .dimensions import DIMS, mapping_columns
from . import quality, semantic

AUTO_DIMENSION_CONFIDENCE = 0.8


def approved_dimensions(db: Session, tenant_id: str) -> dict[str, m.Dataset]:
    out = {}
    for d in db.scalars(select(m.Dataset).where(m.Dataset.tenant_id == tenant_id,
                                                 m.Dataset.model_status == "approved")
                        .order_by(m.Dataset.created_at)):
        role = d.profile.get("role")
        if role in DIMS:
            out[role] = d  # latest approved dataset per role wins
    return out


def refresh_relationships(db: Session, tenant_id: str) -> None:
    """Re-check how well sales join to each approved dimension (runs after any approval, either order)."""
    for role, d in approved_dimensions(db, tenant_id).items():
        stats = quality.relationship_stats(lambda sql: get_backend().scalar(tenant_id, sql), role)
        if stats:
            quality.store_results(db, tenant_id, d.id, "relationship", [quality.relationship_result(stats)])


def build_model(db: Session, tenant_id: str, d: m.Dataset, role: str, mapping: dict[str, str]) -> dict:
    """Validate the mapping, build the modelled table, store quality results. Raises ValueError if it cannot.
    Does not commit: the caller commits (and audits)."""
    cols = {c["name"] for c in d.profile["columns"]}
    allowed = set(semantic.FACT_COLUMNS) if role == "fact_sales" else set(mapping_columns(role))
    for canon, src in mapping.items():
        if canon not in allowed or src not in cols:
            raise ValueError(f"Invalid mapping {canon} -> {src}")
    scalar = lambda sql: get_backend().scalar(tenant_id, sql)  # noqa: E731
    if role == "fact_sales":
        result = get_backend().build_fact_sales(tenant_id, d.table_name, mapping)
        checks = quality.model_checks(result, mapping, scalar)
    else:
        result = get_backend().build_dimension(tenant_id, d.table_name, role, mapping)
        checks = quality.dimension_checks(result)
    d.model_status, d.column_mapping = "approved", mapping
    d.profile = {**d.profile, "model_result": result, "role": role}
    quality.store_results(db, tenant_id, d.id, "model", checks)
    db.flush()  # autoflush is off: make this approval visible to the relationship check
    refresh_relationships(db, tenant_id)
    return result


def plan_auto_model(profile: dict) -> tuple[str, dict[str, str]] | None:
    """Decide (role, mapping) only when the detection is clear; otherwise None (a person confirms)."""
    types = {c["name"]: c["type"] for c in profile["columns"]}
    sd = profile.get("suggested_dimension")
    if sd and sd.get("confidence", 0) >= AUTO_DIMENSION_CONFIDENCE:
        return sd["role"], dict(sd["mapping"])
    sm = profile.get("suggested_mapping") or {}
    if any(v.get("needs_clarification") for v in sm.values()):
        return None
    mapping = {k: v["column"] for k, v in sm.items()}
    if not all(k in mapping for k in ("order_id", "order_date", "net_sales")):
        return None
    if types.get(mapping["order_date"]) != "date" or types.get(mapping["net_sales"]) != "number":
        return None
    if "cost_of_goods_sold" in mapping and types.get(mapping["cost_of_goods_sold"]) != "number":
        del mapping["cost_of_goods_sold"]  # a text "cost" column is not safe to use; the rest still models
    return "fact_sales", mapping


def auto_approve_metrics(db: Session, tenant_id: str) -> list[str]:
    """Approve the built-in metrics whose source columns now exist. Returns the names approved."""
    has_cost = any(d.model_status == "approved" and "cost_of_goods_sold" in (d.column_mapping or {})
                   for d in db.scalars(select(m.Dataset).where(m.Dataset.tenant_id == tenant_id)))
    builtin = {x["name"] for x in semantic.DEFAULT_METRICS}
    done = []
    for metric in db.scalars(select(m.Metric).where(m.Metric.tenant_id == tenant_id, m.Metric.status != "approved")):
        if metric.name not in builtin:
            continue  # metrics people proposed themselves always need an administrator
        try:
            semantic.approve_metric(db, tenant_id, metric.name, has_cost, commit=False)
            done.append(metric.name)
        except semantic.MetricInvalid:
            continue
    return done


def auto_model(db: Session, tenant_id: str, d: m.Dataset) -> None:
    """Called by the worker after a dataset is profiled. Never raises: failure leaves the dataset 'proposed'."""
    tenant = db.get(m.Tenant, tenant_id)
    if tenant is None or not tenant.auto_model:
        return
    planned = plan_auto_model(d.profile)
    if planned is None:
        d.profile = {**d.profile, "auto": {"status": "needs_review",
                                           "reason": "Some columns could not be identified with confidence."}}
        return
    role, mapping = planned
    try:
        with db.begin_nested():
            result = build_model(db, tenant_id, d, role, mapping)
            approved = auto_approve_metrics(db, tenant_id) if role == "fact_sales" else []
            d.profile = {**d.profile, "auto": {"status": "built", "role": role, "mapping": mapping,
                                               "metrics_approved": approved}}
            db.add(m.AuditEvent(tenant_id=tenant_id, action="dataset.auto_approved",
                                detail={"dataset_id": d.id, "role": role, "mapping": mapping, **result}))
            for name in approved:
                db.add(m.AuditEvent(tenant_id=tenant_id, action="metric.auto_approved",
                                    detail={"name": name, "dataset_id": d.id}))
    except Exception as e:  # noqa: BLE001 - a failed automatic build must never fail the upload
        d.model_status = "proposed"
        d.profile = {**d.profile, "auto": {"status": "needs_review", "reason": str(e)[:300]}}
