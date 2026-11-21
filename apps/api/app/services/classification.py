"""Field-level restrictions. Each dimension field and each metric can carry a classification; a person whose role is
below the one it needs cannot group by it, filter on it or ask for it. Checked in the one place every query is compiled
(services/common._compile), so chat, reports, dashboards, alerts and previews all follow the same rule."""
from sqlalchemy.orm import Session

from .. import models as m

LEVELS = ["public", "internal", "confidential", "restricted"]
NEEDS = {"public": "viewer", "internal": "viewer", "confidential": "analyst", "restricted": "admin"}
RANK = {"viewer": 0, "analyst": 1, "admin": 2}
FIELDS = ["product", "region", "customer", "category", "segment"]


class ClassificationInvalid(ValueError):
    pass


def load(db: Session, tenant_id: str) -> dict:
    t = db.get(m.Tenant, tenant_id)
    c = (t.column_classification if t else None) or {}
    return {"fields": dict(c.get("fields", {})), "metrics": dict(c.get("metrics", {}))}


def validate(body: dict, metric_names: set[str]) -> dict:
    out = {"fields": {}, "metrics": {}}
    for kind, allowed in (("fields", set(FIELDS)), ("metrics", metric_names)):
        for name, level in (body.get(kind) or {}).items():
            if name not in allowed:
                raise ClassificationInvalid(f"Unknown {kind[:-1]}: {name}")
            if level not in LEVELS:
                raise ClassificationInvalid(f"Unknown level for {name}: {level}")
            if level != "public":
                out[kind][name] = level
    return out


def _check(level: str | None, role: str, what: str) -> str | None:
    if level and RANK.get(role, 0) < RANK[NEEDS[level]]:
        return f"{what} is classified {level} and needs the {NEEDS[level]} role."
    return None


def refusal(db: Session, tenant_id: str, role: str, metric: str, fields: list[str]) -> str | None:
    c = load(db, tenant_id)
    msg = _check(c["metrics"].get(metric), role, f"The metric {metric}")
    for f in fields:
        msg = msg or _check(c["fields"].get(f), role, f"The field {f}")
    return msg
