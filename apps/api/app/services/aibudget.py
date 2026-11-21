"""Monthly AI token budget per organisation. Counts tokens only (never content). When the budget is used up the
question planner falls back to the built-in rules, so people still get answers; nothing is billed beyond the plan.

The tenant is passed through a context variable so the planner and the embedding call need no extra arguments."""
from contextvars import ContextVar
from datetime import datetime, timezone

from sqlalchemy import func, select

from .. import models as m
from ..db import SessionLocal

_tenant: ContextVar[str | None] = ContextVar("ai_tenant", default=None)


def bind(tenant_id: str | None) -> None:
    _tenant.set(tenant_id)


def current() -> str | None:
    return _tenant.get()


def month_key(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def used(db, tenant_id: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(m.AiUsage.tokens), 0)).where(
        m.AiUsage.tenant_id == tenant_id, m.AiUsage.month == month_key())) or 0)


def budget(db, tenant_id: str) -> int:
    from . import quotas
    return quotas.limits_for(db.get(m.Tenant, tenant_id)).get("ai_tokens", 0)


def exhausted(tenant_id: str | None) -> bool:
    if not tenant_id:
        return False
    with SessionLocal() as db:
        cap = budget(db, tenant_id)
        return bool(cap) and used(db, tenant_id) >= cap


def record(tenant_id: str | None, kind: str, tokens: int | None) -> None:
    """Add the tokens of one model call. Never raises: accounting must not break an answer."""
    if not tenant_id or not tokens:
        return
    try:
        with SessionLocal() as db:
            row = db.scalar(select(m.AiUsage).where(m.AiUsage.tenant_id == tenant_id, m.AiUsage.month == month_key(),
                                                    m.AiUsage.kind == kind))
            if row is None:
                row = m.AiUsage(tenant_id=tenant_id, month=month_key(), kind=kind, tokens=0, calls=0)
                db.add(row)
            row.tokens, row.calls = (row.tokens or 0) + int(tokens), (row.calls or 0) + 1
            db.commit()
    except Exception:  # noqa: BLE001
        pass
