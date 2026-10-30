"""Data-quality rules and schema-version tracking. Results are stored, never silently dropped."""
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Dataset, QualityResult, SchemaVersion

NULL_WARN, NULL_FAIL = 0.10, 0.50
REJECT_FAIL = 0.10
STALE_DAYS = 90

# Thresholds an administrator may change per organisation. The set of checks itself is fixed in code: a customer can
# tune how strict a check is (or switch off the two that are often legitimate), not write new checks.
DEFAULTS = {"null_warn": NULL_WARN, "null_fail": NULL_FAIL, "reject_fail": REJECT_FAIL, "stale_days": STALE_DAYS,
            "negative_sales": "warn", "duplicate_order_ids": "warn"}
SEVERITIES = ("off", "warn", "fail")


class RulesInvalid(ValueError):
    """The thresholds make no sense; the message is shown to the person."""


def validate_rules(raw: dict) -> dict:
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise RulesInvalid("Unknown setting: " + ", ".join(sorted(unknown)))
    cfg = {**DEFAULTS, **raw}
    try:
        nw, nf, rf, sd = float(cfg["null_warn"]), float(cfg["null_fail"]), float(cfg["reject_fail"]), int(cfg["stale_days"])
    except (TypeError, ValueError):
        raise RulesInvalid("Thresholds must be numbers.") from None
    if not 0 <= nw < nf <= 1:
        raise RulesInvalid("Blank-value limits must satisfy 0 <= warn < fail <= 1 (as fractions, e.g. 0.1 for 10%).")
    if not 0 < rf <= 1:
        raise RulesInvalid("The quarantine limit must be above 0 and at most 1 (e.g. 0.1 for 10%).")
    if not 1 <= sd <= 3650:
        raise RulesInvalid("Stale after must be between 1 and 3650 days.")
    for k in ("negative_sales", "duplicate_order_ids"):
        if cfg[k] not in SEVERITIES:
            raise RulesInvalid(f"{k} must be one of {', '.join(SEVERITIES)}.")
    return {"null_warn": nw, "null_fail": nf, "reject_fail": rf, "stale_days": sd,
            "negative_sales": cfg["negative_sales"], "duplicate_order_ids": cfg["duplicate_order_ids"]}


def rules_for(tenant) -> dict:
    """The organisation's settings over the defaults; a stored value that no longer validates falls back to defaults."""
    try:
        return validate_rules(dict(getattr(tenant, "quality_settings", None) or {}))
    except RulesInvalid:
        return dict(DEFAULTS)


def _sev(base: str, count: int, label: str) -> str:
    """pass when nothing is found, otherwise the configured severity ('off' never reaches here)."""
    return "pass" if not count else base


def _r(rule, status, message, **observed):
    return {"rule": rule, "status": status, "message": message, "observed": observed}


def profile_checks(profile: dict, drift: dict | None = None, cfg: dict | None = None) -> list[dict]:
    cfg = cfg or DEFAULTS
    n = profile["row_count"]
    out = [_r("non_empty", "pass" if n > 0 else "fail",
              f"{n} rows" if n else "The file has no data rows", rows=n)]
    dup = profile.get("duplicate_rows", 0)
    out.append(_r("duplicate_rows", "pass" if not dup else "warn",
                  "No fully duplicated rows" if not dup else f"{dup} fully duplicated rows", count=dup))
    for c in profile["columns"]:
        rate = c["null_count"] / n if n else 0.0
        st = "pass" if rate <= cfg["null_warn"] else ("warn" if rate <= cfg["null_fail"] else "fail")
        if st != "pass":
            out.append(_r(f"completeness:{c['name']}", st,
                          f"{c['name']} is blank in {rate:.0%} of rows", null_rate=round(rate, 4)))
    if drift and (drift.get("removed") or drift.get("type_changed")):
        out.append(_r("schema_drift", "warn", "Columns were removed or changed type since the previous upload",
                      **drift))
    elif drift is not None:
        out.append(_r("schema_drift", "pass", "Schema is compatible with the previous upload", **drift))
    return out


def model_checks(result: dict, mapping: dict, scalar, today: date | None = None, cfg: dict | None = None) -> list[dict]:
    """scalar(sql) runs a read on the tenant's fact_sales. result comes from build_fact_sales."""
    cfg = cfg or DEFAULTS
    today = today or date.today()
    loaded, rejected = result["rows_loaded"], result["rows_rejected"]
    total = loaded + rejected
    out = [_r("reconciliation", "pass" if result.get("reconciled", True) else "fail",
              f"{loaded} loaded + {rejected} quarantined = {total} staged", loaded=loaded, rejected=rejected)]
    if result.get("rows_replaced"):
        out.append(_r("replaced_by_later_file", "warn",
                      f"{result.get('orders_replaced', 0)} orders ({result['rows_replaced']} rows) from earlier files were replaced "
                      "because this file contains the same order IDs (the latest file wins). "
                      "If that is not what you intended, check which files overlap.",
                      orders=result.get("orders_replaced", 0), rows=result["rows_replaced"]))
    rate = rejected / total if total else 0.0
    out.append(_r("rejection_rate", "pass" if not rejected else ("fail" if rate > cfg["reject_fail"] else "warn"),
                  "No rows quarantined" if not rejected else f"{rejected} rows ({rate:.0%}) failed type checks and were quarantined",
                  rate=round(rate, 4)))
    dup_orders = int(scalar("SELECT count(*) - count(DISTINCT order_id) FROM fact_sales") or 0)
    if cfg["duplicate_order_ids"] != "off":
        out.append(_r("duplicate_order_ids", _sev(cfg["duplicate_order_ids"], dup_orders, ""),
                      "Order IDs are unique" if not dup_orders else
                      f"{dup_orders} repeated order IDs (fine for multi-line orders; otherwise sales may be double counted)",
                      count=dup_orders))
    neg = int(scalar("SELECT count(*) FROM fact_sales WHERE net_sales < 0") or 0)
    if cfg["negative_sales"] != "off":
        out.append(_r("negative_sales", _sev(cfg["negative_sales"], neg, ""),
                      "No negative sales values" if not neg else f"{neg} rows have negative net sales (refunds?)", count=neg))
    if "cost_of_goods_sold" in mapping:
        miss = int(scalar("SELECT count(*) FROM fact_sales WHERE cost_of_goods_sold IS NULL") or 0)
        out.append(_r("cost_coverage", "pass" if not miss else "warn",
                      "Every row has a cost" if not miss else f"{miss} rows have no cost; profit is incomplete for them",
                      missing=miss))
    latest = scalar("SELECT max(order_date) FROM fact_sales")
    if latest:
        d = date.fromisoformat(str(latest)[:10])
        stale = d < today - timedelta(days=cfg["stale_days"])
        out.append(_r("freshness", "warn" if stale else "pass",
                      f"Latest order date is {d.isoformat()}" + (f" (older than {cfg['stale_days']} days)" if stale else ""),
                      latest_order_date=d.isoformat()))
    return out


def store_results(db: Session, tenant_id: str, dataset_id: str, stage: str, results: list[dict]) -> None:
    db.execute(delete(QualityResult).where(QualityResult.tenant_id == tenant_id,
                                           QualityResult.dataset_id == dataset_id,
                                           QualityResult.stage == stage))
    for r in results:
        db.add(QualityResult(tenant_id=tenant_id, dataset_id=dataset_id, stage=stage, rule=r["rule"],
                             status=r["status"], message=r["message"], observed=r["observed"]))


def record_schema_version(db: Session, ds: Dataset, source_id: str, profile: dict) -> dict:
    cols = [{"name": c["name"], "type": c["type"]} for c in profile["columns"]]
    prev = db.scalar(select(SchemaVersion).where(
        SchemaVersion.tenant_id == ds.tenant_id, SchemaVersion.source_id == source_id,
        SchemaVersion.dataset_name == ds.name).order_by(SchemaVersion.version.desc()))
    changes: dict = {"added": [], "removed": [], "type_changed": []}
    if prev:
        old = {c["name"]: c["type"] for c in prev.columns}
        new = {c["name"]: c["type"] for c in cols}
        changes = {"added": sorted(set(new) - set(old)), "removed": sorted(set(old) - set(new)),
                   "type_changed": sorted(k for k in new if k in old and new[k] != old[k])}
    db.add(SchemaVersion(tenant_id=ds.tenant_id, dataset_id=ds.id, source_id=source_id, dataset_name=ds.name,
                         version=(prev.version + 1) if prev else 1, columns=cols, changes=changes))
    return changes if prev else None


def dimension_checks(result: dict) -> list[dict]:
    loaded, rejected = result["rows_loaded"], result["rows_rejected"]
    total = loaded + rejected
    out = [_r("reconciliation", "pass", f"{loaded} loaded + {rejected} quarantined = {total} staged",
              loaded=loaded, rejected=rejected),
           _r("key_unique", "pass" if not rejected else "warn",
              "Every key is present and unique" if not rejected else
              f"{rejected} rows had a blank or repeated key and were quarantined (the first value per key is kept)",
              rejected=rejected)]
    miss = result.get("attribute_missing_rows", 0)
    out.append(_r("attribute_coverage", "pass" if not miss else "warn",
                  "Every key has a category/segment" if not miss else f"{miss} keys have no category/segment",
                  missing=miss))
    return out


def relationship_stats(scalar, role: str) -> dict | None:
    """How much of the sales data joins to this dimension. None if sales are not modelled yet."""
    from .dimensions import DIMS
    d = DIMS[role]
    fc, t, k = d["fact_col"], d["table"], d["key_col"]
    try:
        keys = int(scalar(f"SELECT count(DISTINCT {fc}) FROM fact_sales WHERE {fc} IS NOT NULL") or 0)
        matched = int(scalar(f"SELECT count(DISTINCT f.{fc}) FROM fact_sales f JOIN {t} d ON f.{fc} = d.{k}") or 0)
        rows = int(scalar(f"SELECT count(*) FROM fact_sales WHERE {fc} IS NOT NULL") or 0)
        rows_ok = int(scalar(f"SELECT count(*) FROM fact_sales f JOIN {t} d ON f.{fc} = d.{k}") or 0)
    except Exception:  # sales not modelled yet
        return None
    pct = matched / keys if keys else 1.0
    return {"role": role, "label": d["label"], "fact_column": fc, "dimension_key": k,
            "sales_keys": keys, "matched_keys": matched, "orphan_keys": keys - matched,
            "key_coverage": round(pct, 4), "rows_unassigned": rows - rows_ok,
            "status": "pass" if keys == matched else ("warn" if pct >= 0.5 else "fail")}


def relationship_result(stats: dict) -> dict:
    msg = (f"All {stats['sales_keys']} {stats['fact_column']} values in sales match the {stats['label'].lower()} list"
           if not stats["orphan_keys"] else
           f"{stats['orphan_keys']} of {stats['sales_keys']} {stats['fact_column']} values in sales have no match; "
           f"{stats['rows_unassigned']} sales rows are shown as 'Unassigned'")
    return _r("join_coverage", stats["status"], msg, **{k: v for k, v in stats.items() if k not in ("status", "label")})
