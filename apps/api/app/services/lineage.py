"""Lineage: where a number comes from, from the uploaded file to the metric and the things that use it.

Everything is read from records the platform already keeps (files with their checksums, datasets and their approved
column mappings, metric definitions, reports, alerts, answered questions). Nothing here is guessed: a link exists
only if the records say so, and every query is scoped to the caller's organisation.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from .dimensions import DIMS
from .semantic import MetricInvalid, validate_expression

DIM_COLUMN_FOR = {"product": "dim_product", "customer": "dim_customer"}   # metric dimension -> dimension model


def _file_json(f: m.SourceFile | None, source: m.DataSource | None) -> dict | None:
    if f is None:
        return None
    return {"id": f.id, "filename": f.filename, "sha256": f.sha256, "version": f.version,
            "uploaded_at": f.created_at.isoformat(), "source": source.name if source else None}


def _latest_metrics(db: Session, tenant_id: str) -> dict[str, m.Metric]:
    out: dict[str, m.Metric] = {}
    for x in db.scalars(select(m.Metric).where(m.Metric.tenant_id == tenant_id).order_by(m.Metric.version)):
        out[x.name] = x  # latest version wins
    return out


def _approved_by_role(db: Session, tenant_id: str) -> dict[str, list[m.Dataset]]:
    """Approved datasets per model role. Sales files accumulate, so several datasets can feed fact_sales."""
    out: dict[str, list[m.Dataset]] = {}
    for d in db.scalars(select(m.Dataset).where(m.Dataset.tenant_id == tenant_id, m.Dataset.model_status == "approved")
                        .order_by(m.Dataset.created_at)):
        out.setdefault(d.profile.get("role") or "fact_sales", []).append(d)
    return out


def metric_trace(db: Session, tenant_id: str, name: str) -> dict | None:
    """Upstream and downstream of one metric. None if the metric does not exist in this organisation."""
    metric = _latest_metrics(db, tenant_id).get(name)
    if metric is None:
        return None
    files = {f.id: f for f in db.scalars(select(m.SourceFile).where(m.SourceFile.tenant_id == tenant_id))}
    sources = {s.id: s for s in db.scalars(select(m.DataSource).where(m.DataSource.tenant_id == tenant_id))}
    roles = _approved_by_role(db, tenant_id)
    facts = roles.get("fact_sales", [])

    try:
        used = sorted(validate_expression(metric.expression))
    except MetricInvalid:
        used = []
    columns = []
    for canon in used:
        # the latest approved sales file that maps this field is the one the numbers come from
        feeders = [d for d in facts if canon in (d.column_mapping or {})]
        d = feeders[-1] if feeders else None
        f = files.get(d.file_id) if d else None
        columns.append({
            "field": canon,
            "source_column": (d.column_mapping or {}).get(canon) if d else None,
            "dataset": {"id": d.id, "name": d.name} if d else None,
            "file": _file_json(f, sources.get(f.source_id) if f else None),
            "mapped": d is not None,
        })

    models_ = []
    for role, label in (("fact_sales", "Sales (fact table)"), *[(r, f"{DIMS[r]['label']} list") for r in DIMS]):
        ds = roles.get(role, [])
        if role != "fact_sales" and not any(DIM_COLUMN_FOR.get(dim) == role for dim in metric.dimensions or []):
            continue  # a dimension table matters only if this metric can be broken down by it
        models_.append({
            "role": role, "label": label,
            "built": bool(ds),
            "rows": (ds[-1].profile.get("model_result") or {}).get("rows_loaded") if ds else None,
            "datasets": [{"id": d.id, "name": d.name,
                          "file": _file_json(files.get(d.file_id), sources.get(files[d.file_id].source_id)
                                             if d.file_id in files else None)} for d in ds],
        })

    reports = [r for r in db.scalars(select(m.Report).where(m.Report.tenant_id == tenant_id).order_by(m.Report.created_at))
              if (r.plan or {}).get("metric") == name]
    alerts = list(db.scalars(select(m.Alert).where(m.Alert.tenant_id == tenant_id, m.Alert.metric == name)))
    since = datetime.now(timezone.utc) - timedelta(days=30)
    answers = 0
    for q in db.scalars(select(m.AnalyticalQuery).where(m.AnalyticalQuery.tenant_id == tenant_id,
                                                         m.AnalyticalQuery.outcome == "answered",
                                                         m.AnalyticalQuery.created_at >= since).limit(5000)):
        if (q.plan or {}).get("metric") == name:
            answers += 1

    complete = bool(columns) and all(c["mapped"] for c in columns)
    return {
        "metric": {"name": metric.name, "label": metric.label, "description": metric.description,
                   "expression": metric.expression, "aggregation": metric.aggregation, "status": metric.status,
                   "version": metric.version, "dimensions": metric.dimensions or []},
        "columns": columns, "models": models_,
        "used_by": {"reports": [{"id": r.id, "title": r.title} for r in reports],
                    "alerts": [{"id": a.id, "name": a.name} for a in alerts],
                    "answers_last_30_days": answers},
        "complete": complete,
        "note": None if complete else
        "Some source fields are not mapped to a column yet, so this metric cannot be calculated or approved.",
    }


def graph(db: Session, tenant_id: str) -> dict:
    """The whole chain as nodes and edges: source -> file -> dataset -> model -> metric -> report / alert."""
    nodes: list[dict] = []
    edges: list[dict] = []

    def node(nid: str, kind: str, label: str, **extra):
        nodes.append({"id": nid, "kind": kind, "label": label, **extra})

    def edge(a: str, b: str, label: str = ""):
        edges.append({"from": a, "to": b, "label": label})

    sources = list(db.scalars(select(m.DataSource).where(m.DataSource.tenant_id == tenant_id).order_by(m.DataSource.created_at)))
    files = list(db.scalars(select(m.SourceFile).where(m.SourceFile.tenant_id == tenant_id).order_by(m.SourceFile.created_at)))
    datasets = list(db.scalars(select(m.Dataset).where(m.Dataset.tenant_id == tenant_id).order_by(m.Dataset.created_at)))
    metrics = _latest_metrics(db, tenant_id)
    roles = _approved_by_role(db, tenant_id)

    for s in sources:
        node(f"source:{s.id}", "source", s.name)
    for f in files:
        node(f"file:{f.id}", "file", f.filename, sha256=f.sha256[:12], version=f.version,
             uploaded_at=f.created_at.isoformat())
        edge(f"source:{f.source_id}", f"file:{f.id}", "uploaded")
    for d in datasets:
        node(f"dataset:{d.id}", "dataset", d.name, model_status=d.model_status, rows=d.profile.get("row_count"))
        edge(f"file:{d.file_id}", f"dataset:{d.id}", "profiled")
    for role, ds in roles.items():
        node(f"model:{role}", "model", role, rows=(ds[-1].profile.get("model_result") or {}).get("rows_loaded"))
        for d in ds:
            edge(f"dataset:{d.id}", f"model:{role}", "approved mapping")
    for name, x in metrics.items():
        node(f"metric:{name}", "metric", x.label, status=x.status, expression=x.expression)
        if "fact_sales" in roles:
            edge("model:fact_sales", f"metric:{name}", "calculated from")
        for dim in x.dimensions or []:
            role = DIM_COLUMN_FOR.get(dim)
            if role and role in roles:
                edge(f"model:{role}", f"metric:{name}", f"by {dim}")
    for r in db.scalars(select(m.Report).where(m.Report.tenant_id == tenant_id)):
        mn = (r.plan or {}).get("metric")
        node(f"report:{r.id}", "report", r.title)
        if mn in metrics:
            edge(f"metric:{mn}", f"report:{r.id}", "shown in")
    for a in db.scalars(select(m.Alert).where(m.Alert.tenant_id == tenant_id)):
        node(f"alert:{a.id}", "alert", a.name)
        if a.metric in metrics:
            edge(f"metric:{a.metric}", f"alert:{a.id}", "watched by")
    return {"nodes": nodes, "edges": edges}
