"""Service-owner report across all organisations: activation rate, median time to first dashboard, 7/30-day
retention, AI tokens. Counts only; reads the control-plane database named by DATABASE_URL.

    cd apps/api && python ../../scripts/product_report.py
"""
import json
import statistics
import sys

sys.path.insert(0, ".")
from sqlalchemy import select  # noqa: E402

from app import models as m  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.services import product_analytics  # noqa: E402

with SessionLocal() as db:
    rows = [product_analytics.tenant_metrics(db, t) for t in db.scalars(select(m.Tenant.id))]
n = len(rows)
dash = [r["minutes_to_first_dashboard"] for r in rows if r["minutes_to_first_dashboard"] is not None]
out = {
    "organisations": n,
    "activated": sum(r["activated"] for r in rows),
    "activation_rate": round(sum(r["activated"] for r in rows) / n, 3) if n else None,
    "median_minutes_to_first_dashboard": statistics.median(dash) if dash else None,
    "active_7d": sum(r["active_people_7d"] > 0 for r in rows),
    "active_30d": sum(r["active_people_30d"] > 0 for r in rows),
    "ai_tokens_this_month": sum(r["cost_to_serve_30d"]["ai_tokens_this_month"] for r in rows),
    "questions_30d": sum(r["cost_to_serve_30d"]["questions"] for r in rows),
}
print(json.dumps(out, indent=2))
