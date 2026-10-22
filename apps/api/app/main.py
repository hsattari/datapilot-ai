import hashlib
from datetime import date

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .config import get_settings
from .db import Base, engine, get_db
from .deps import Principal, get_principal, require_admin, require_writer
from .routers.knowledge import router as knowledge_router
from .schemas import ApproveIn, LoginIn, QuestionIn, RegisterIn, SourceIn
from .security import create_token, hash_password, verify_password
from .services.backends import get_backend
from .services.dimensions import ATTR_TO_ROLE, DIMS, mapping_columns
from .services import quality, semantic
from .services.planner import make_plan
from .services.query_engine import QueryRejected, compile_plan, execute_readonly
from .services.storage import get_storage, safe_filename
from .services.validation import UploadRejected, validate_upload

app = FastAPI(title="DataPilot AI API", version="0.1.0")
app.include_router(knowledge_router)


def audit(db: Session, p: Principal | None, action: str, **detail) -> None:
    db.add(m.AuditEvent(tenant_id=p.tenant_id if p else None, user_id=p.user_id if p else None,
                        action=action, detail=detail))


def owned(db: Session, model, obj_id: str, tenant_id: str):
    """Fetch an object only if it belongs to the caller's tenant; 404 otherwise (no existence leak)."""
    obj = db.get(model, obj_id)
    if obj is None or obj.tenant_id != tenant_id:
        raise HTTPException(404, "Not found")
    return obj


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/api/v1/tenants", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if db.scalar(select(m.User).where(m.User.email == body.email.lower())):
        raise HTTPException(409, "Email already registered")
    tenant = m.Tenant(name=body.organisation, currency=body.currency)
    user = m.User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add_all([tenant, user])
    db.flush()
    db.add(m.TenantMembership(tenant_id=tenant.id, user_id=user.id, role="admin"))
    semantic.seed_metrics(db, tenant.id)
    db.add(m.AuditEvent(tenant_id=tenant.id, user_id=user.id, action="tenant.created", detail={}))
    db.commit()
    return {"tenant_id": tenant.id, "token": create_token(user.id)}


@app.post("/api/v1/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(m.User).where(m.User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    return {"token": create_token(user.id)}


@app.post("/api/v1/data-sources", status_code=201)
def create_source(body: SourceIn, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    s = m.DataSource(tenant_id=p.tenant_id, name=body.name)
    db.add(s)
    audit(db, p, "source.created", name=body.name)
    db.commit()
    return {"id": s.id, "name": s.name}


@app.post("/api/v1/data-sources/{source_id}/uploads", status_code=202)
async def upload(source_id: str, file: UploadFile = File(...), p: Principal = Depends(require_writer),
                 db: Session = Depends(get_db)):
    owned(db, m.DataSource, source_id, p.tenant_id)
    cfg = get_settings()
    data = await file.read(cfg.max_upload_mb * 1024 * 1024 + 1)
    name = safe_filename(file.filename or "upload")
    try:
        validate_upload(name, data, cfg.max_upload_mb)
    except UploadRejected as e:
        audit(db, p, "upload.rejected", filename=name, reason=str(e))
        db.commit()
        raise HTTPException(422, str(e))
    sha = hashlib.sha256(data).hexdigest()
    version = 1 + (db.scalar(select(m.SourceFile.version).where(
        m.SourceFile.tenant_id == p.tenant_id, m.SourceFile.source_id == source_id,
        m.SourceFile.filename == name).order_by(m.SourceFile.version.desc())) or 0)
    sf = m.SourceFile(tenant_id=p.tenant_id, source_id=source_id, filename=name, sha256=sha,
                      size_bytes=len(data), storage_key="", version=version)
    db.add(sf)
    db.flush()
    sf.storage_key = f"tenants/{p.tenant_id}/{source_id}/{sf.id}/{name}"
    get_storage().put(sf.storage_key, data)  # immutable raw copy (bronze)
    job = m.IngestionJob(tenant_id=p.tenant_id, file_id=sf.id)
    db.add(job)
    audit(db, p, "upload.accepted", file_id=sf.id, sha256=sha)
    db.commit()
    return {"file_id": sf.id, "job_id": job.id, "sha256": sha, "version": version}


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    j = owned(db, m.IngestionJob, job_id, p.tenant_id)
    return {"id": j.id, "status": j.status, "attempts": j.attempts, "error_category": j.error_category,
            "error_message": j.error_message, "rows_loaded": j.rows_loaded}


@app.get("/api/v1/datasets")
def list_datasets(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(m.Dataset).where(m.Dataset.tenant_id == p.tenant_id)).all()
    return [{"id": d.id, "name": d.name, "model_status": d.model_status, "role": d.profile.get("role"),
             "rows": d.profile.get("row_count")} for d in rows]


@app.get("/api/v1/datasets/{dataset_id}/profile")
def dataset_profile(dataset_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    d = owned(db, m.Dataset, dataset_id, p.tenant_id)
    return {"id": d.id, "name": d.name, "model_status": d.model_status, "profile": d.profile}


@app.get("/api/v1/datasets/{dataset_id}/quality")
def dataset_quality(dataset_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    d = owned(db, m.Dataset, dataset_id, p.tenant_id)
    rows = db.scalars(select(m.QualityResult).where(
        m.QualityResult.tenant_id == p.tenant_id, m.QualityResult.dataset_id == d.id)
        .order_by(m.QualityResult.stage, m.QualityResult.rule)).all()
    results = [{"stage": r.stage, "rule": r.rule, "status": r.status, "message": r.message,
                "observed": r.observed} for r in rows]
    counts = {k: sum(1 for r in results if r["status"] == k) for k in ("pass", "warn", "fail")}
    overall = "fail" if counts["fail"] else ("warn" if counts["warn"] else "pass")
    return {"dataset_id": d.id, "overall": overall, "counts": counts, "results": results}


@app.get("/api/v1/datasets/{dataset_id}/schema-versions")
def dataset_schema_versions(dataset_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    d = owned(db, m.Dataset, dataset_id, p.tenant_id)
    cur = db.scalar(select(m.SchemaVersion).where(m.SchemaVersion.tenant_id == p.tenant_id,
                                                  m.SchemaVersion.dataset_id == d.id))
    if cur is None:
        return {"versions": []}
    rows = db.scalars(select(m.SchemaVersion).where(
        m.SchemaVersion.tenant_id == p.tenant_id, m.SchemaVersion.source_id == cur.source_id,
        m.SchemaVersion.dataset_name == cur.dataset_name).order_by(m.SchemaVersion.version)).all()
    return {"versions": [{"version": r.version, "dataset_id": r.dataset_id, "columns": r.columns,
                          "changes": r.changes, "created_at": r.created_at.isoformat()} for r in rows]}


@app.post("/api/v1/datasets/{dataset_id}/approve")
def approve_dataset(dataset_id: str, body: ApproveIn, p: Principal = Depends(require_writer),
                    db: Session = Depends(get_db)):
    d = owned(db, m.Dataset, dataset_id, p.tenant_id)
    cols = {c["name"] for c in d.profile["columns"]}
    allowed = set(semantic.FACT_COLUMNS) if body.role == "fact_sales" else set(mapping_columns(body.role))
    for canon, src in body.mapping.items():
        if canon not in allowed or src not in cols:
            raise HTTPException(422, f"Invalid mapping {canon} -> {src}")
    scalar = lambda sql: get_backend().scalar(p.tenant_id, sql)  # noqa: E731
    try:
        if body.role == "fact_sales":
            result = get_backend().build_fact_sales(p.tenant_id, d.table_name, body.mapping)
            checks = quality.model_checks(result, body.mapping, scalar)
        else:
            result = get_backend().build_dimension(p.tenant_id, d.table_name, body.role, body.mapping)
            checks = quality.dimension_checks(result)
    except ValueError as e:
        raise HTTPException(422, str(e))
    d.model_status, d.column_mapping = "approved", body.mapping
    d.profile = {**d.profile, "model_result": result, "role": body.role}
    quality.store_results(db, p.tenant_id, d.id, "model", checks)
    db.flush()  # autoflush is off: make this approval visible to the relationship check
    _refresh_relationships(db, p)
    audit(db, p, "dataset.approved", dataset_id=d.id, **{**result, "role": body.role})
    db.commit()
    return result


def _approved_dimensions(db: Session, tenant_id: str) -> dict[str, m.Dataset]:
    out = {}
    for d in db.scalars(select(m.Dataset).where(m.Dataset.tenant_id == tenant_id,
                                                 m.Dataset.model_status == "approved")
                        .order_by(m.Dataset.created_at)):
        role = d.profile.get("role")
        if role in DIMS:
            out[role] = d  # latest approved dataset per role wins
    return out


def _refresh_relationships(db: Session, p: Principal) -> None:
    """Re-check how well sales join to each approved dimension (runs after any approval, either order)."""
    for role, d in _approved_dimensions(db, p.tenant_id).items():
        stats = quality.relationship_stats(lambda sql: get_backend().scalar(p.tenant_id, sql), role)
        if stats:
            quality.store_results(db, p.tenant_id, d.id, "relationship", [quality.relationship_result(stats)])


@app.get("/api/v1/relationships")
def relationships(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Joins between sales and approved dimension tables, with live key coverage."""
    out = []
    for role, d in _approved_dimensions(db, p.tenant_id).items():
        stats = quality.relationship_stats(lambda sql: get_backend().scalar(p.tenant_id, sql), role)
        out.append({"dataset_id": d.id, "dataset": d.name, "role": role, "sales_modelled": stats is not None,
                    **(stats or {})})
    return {"relationships": out}


@app.get("/api/v1/metrics")
def list_metrics(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.scalars(select(m.Metric).where(m.Metric.tenant_id == p.tenant_id)).all()
    return [{"name": x.name, "label": x.label, "description": x.description, "expression": x.expression,
             "aggregation": x.aggregation, "status": x.status, "version": x.version} for x in rows]


@app.post("/api/v1/metrics/{name}/approve")
def approve_metric(name: str, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    has_cost = any(d.model_status == "approved" and "cost_of_goods_sold" in d.column_mapping
                   for d in db.scalars(select(m.Dataset).where(m.Dataset.tenant_id == p.tenant_id)))
    try:
        metric = semantic.approve_metric(db, p.tenant_id, name, has_cost)
    except semantic.MetricInvalid as e:
        raise HTTPException(422, str(e))
    audit(db, p, "metric.approved", name=name)
    db.commit()
    return {"name": metric.name, "status": metric.status}


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


@app.post("/api/v1/analytics/query")
def analytics_query(body: QuestionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    approved = {x.name: x.description for x in db.scalars(select(m.Metric).where(
        m.Metric.tenant_id == p.tenant_id, m.Metric.status == "approved"))}
    res = make_plan(body.question, approved)
    if res.outcome != "plan":
        db.add(m.AnalyticalQuery(tenant_id=p.tenant_id, user_id=p.user_id, question=body.question[:1000],
                                 outcome=res.outcome))
        audit(db, p, f"query.{res.outcome}")
        db.commit()
        return {"outcome": res.outcome, "message": res.message}
    try:
        out = _run_plan(db, p, res.plan)
    except QueryRejected as e:
        audit(db, p, "query.rejected", reason=str(e))
        db.commit()
        return {"outcome": "refusal", "message": str(e)}
    except Exception:
        return {"outcome": "clarification",
                "message": "No approved data model is available yet. Upload and approve a dataset first."}
    db.add(m.AnalyticalQuery(tenant_id=p.tenant_id, user_id=p.user_id, question=body.question[:1000],
                             plan=res.plan.model_dump(), sql_text=out["sql"], outcome="answered"))
    audit(db, p, "query.answered", metric=res.plan.metric)
    db.commit()
    return {"outcome": "answered", "plan": res.plan.model_dump(), **out}


@app.get("/api/v1/dashboard")
def dashboard(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Native dashboard: approved metrics only, same engine and definitions as chat answers."""
    from .services.query_engine import Plan
    tiles = []
    for x in db.scalars(select(m.Metric).where(m.Metric.tenant_id == p.tenant_id,
                                               m.Metric.status == "approved")):
        try:
            total = _run_plan(db, p, Plan(intent="metric_total", metric=x.name, visualization="number"))
            trend = _run_plan(db, p, Plan(intent="compare_metric_over_time", metric=x.name,
                                          dimensions=["month"], visualization="line_chart"))
        except Exception:
            continue
        tiles.append({"metric": x.name, "label": x.label, "total": total["rows"][0][0] if total["rows"] else None,
                      "trend": trend["rows"], "caveats": total["caveats"][1:]})
    return {"tiles": tiles}


@app.get("/api/v1/export/powerbi")
def export_powerbi(p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    """Semantic model starting point (TMSL + DAX) generated from approved metrics only."""
    from .services.powerbi import build_export
    rows = db.scalars(select(m.Metric).where(m.Metric.tenant_id == p.tenant_id)).all()
    out = build_export([{"name": x.name, "label": x.label, "description": x.description,
                         "expression": x.expression, "aggregation": x.aggregation, "status": x.status,
                         "version": x.version} for x in rows])
    audit(db, p, "export.powerbi", measures=len(out["model_bim"]["model"]["tables"][0]["measures"]))
    db.commit()
    return out
