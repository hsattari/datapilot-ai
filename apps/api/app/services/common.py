"""Helpers shared by the API modules: audit trail, tenant-scoped lookup, governed plan execution."""
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..deps import Principal
from ..observability import span_error, tracer
from .backends import get_backend
from .dimensions import ATTR_TO_ROLE
from .query_engine import QueryRejected, compile_plan, execute_readonly


def audit(db: Session, p: Principal | None, action: str, **detail) -> None:
    db.add(m.AuditEvent(tenant_id=p.tenant_id if p else None, user_id=p.user_id if p else None,
                        action=action, detail=detail))


def owned(db: Session, model, obj_id: str, tenant_id: str):
    """Fetch an object only if it belongs to the caller's tenant; 404 otherwise (no existence leak)."""
    obj = db.get(model, obj_id)
    if obj is None or obj.tenant_id != tenant_id:
        raise HTTPException(404, "Not found")
    return obj


def run_plan(db: Session, p: Principal, plan, today: date | None = None) -> dict:
    with tracer.start_as_current_span("analytics.run_plan", record_exception=False, set_status_on_exception=False) as span:
        span.set_attribute("dp.tenant_id", p.tenant_id)
        span.set_attribute("dp.metric", str(plan.metric))
        span.set_attribute("dp.dimensions", ",".join(plan.dimensions))
        try:
            out = _run_plan(db, p, plan, today)
        except Exception as e:  # type only: the message may quote data
            span_error(span, e)
            raise
        span.set_attribute("dp.rows", len(out["rows"]))
        return out


def _run_plan(db: Session, p: Principal, plan, today: date | None = None) -> dict:
    metric = db.scalar(select(m.Metric).where(m.Metric.tenant_id == p.tenant_id,
                                              m.Metric.name == plan.metric,
                                              m.Metric.status == "approved"))
    if metric is None:
        raise QueryRejected("Metric is not approved")
    for dim in plan.dimensions:
        if dim not in metric.dimensions and dim not in ATTR_TO_ROLE:  # dimension-table attributes apply to every metric
            raise QueryRejected(f"Dimension not allowed for metric: {dim}")
    backend = get_backend()
    needs_dims = any(a in plan.dimensions or any(f.field == a for f in plan.filters) for a in ATTR_TO_ROLE)
    sql, params = compile_plan(plan, metric.expression, metric.aggregation, today, backend.month_expr("order_date"),
                               backend.tables(p.tenant_id) if needs_dims else None)
    cols, rows = execute_readonly(p.tenant_id, sql, params)
    caveats = ["Associations shown here do not establish causation."]
    fresh = backend.scalar(p.tenant_id, "SELECT max(order_date) FROM fact_sales")
    missing_cost = backend.scalar(p.tenant_id, "SELECT count(*) FROM fact_sales WHERE cost_of_goods_sold IS NULL")
    if "cost_of_goods_sold" in metric.expression and missing_cost:
        caveats.append(f"{missing_cost} order rows have no cost data; profit is incomplete for them.")
    return {"columns": cols, "rows": rows, "sql": sql,
            "metric": {"name": metric.name, "label": metric.label, "definition": metric.description,
                       "expression": metric.expression, "version": metric.version},
            "freshness": {"latest_order_date": str(fresh) if fresh else None},
            "caveats": caveats}
