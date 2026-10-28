"""Answering one question: plan, check, run, record. Shared by the single-question endpoint and conversations.

`prev` is the previous plan in a conversation (or None). It only ever lets a short follow-up such as "and by region?"
be read as a change to that plan; the result is validated and policy-checked exactly like any other plan.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..deps import Principal
from . import insights, quotas
from .common import audit, run_plan
from .planner import make_plan
from .query_engine import QueryRejected


def answer_question(db: Session, p: Principal, question: str, prev: dict | None = None) -> dict:
    quotas.enforce(db, p.tenant_id, "questions")
    approved = {x.name: x.description for x in db.scalars(select(m.Metric).where(
        m.Metric.tenant_id == p.tenant_id, m.Metric.status == "approved"))}
    unapproved = {x.name: x.description for x in db.scalars(select(m.Metric).where(
        m.Metric.tenant_id == p.tenant_id, m.Metric.status != "approved"))}
    res = make_plan(question, approved, unapproved, prev)
    if res.outcome == "explain":
        try:
            out = insights.explain_change(db, p, res.metric, res.months)
        except insights.NotEnoughData as e:
            db.add(m.AnalyticalQuery(tenant_id=p.tenant_id, user_id=p.user_id, question=question[:1000],
                                     outcome="clarification"))
            audit(db, p, "query.clarification")
            db.commit()
            return {"outcome": "clarification", "message": str(e), "planner": res.source}
        except QueryRejected as e:
            audit(db, p, "query.rejected", reason=str(e))
            db.commit()
            return {"outcome": "refusal", "message": str(e)}
        except Exception:
            return {"outcome": "clarification",
                    "message": "No approved data model is available yet. Upload and approve a dataset first."}
        db.add(m.AnalyticalQuery(tenant_id=p.tenant_id, user_id=p.user_id, question=question[:1000],
                                 plan={"intent": "explain_change", "metric": res.metric, "months": res.months},
                                 sql_text="\n".join(out["sql"]), outcome="answered"))
        audit(db, p, "query.explained", metric=res.metric)
        db.commit()
        return {"outcome": "explanation", "planner": res.source, **out}
    if res.outcome != "plan":
        db.add(m.AnalyticalQuery(tenant_id=p.tenant_id, user_id=p.user_id, question=question[:1000],
                                 outcome=res.outcome))
        audit(db, p, f"query.{res.outcome}")
        db.commit()
        return {"outcome": res.outcome, "message": res.message, "planner": res.source}
    try:
        out = run_plan(db, p, res.plan)
    except QueryRejected as e:
        audit(db, p, "query.rejected", reason=str(e))
        db.commit()
        return {"outcome": "refusal", "message": str(e)}
    except Exception:
        return {"outcome": "clarification",
                "message": "No approved data model is available yet. Upload and approve a dataset first."}
    db.add(m.AnalyticalQuery(tenant_id=p.tenant_id, user_id=p.user_id, question=question[:1000],
                             plan=res.plan.model_dump(), sql_text=out["sql"], outcome="answered"))
    audit(db, p, "query.answered", metric=res.plan.metric)
    db.commit()
    return {"outcome": "answered", "plan": res.plan.model_dump(), "planner": res.source,
            **({"interpreted": res.interpreted} if res.interpreted else {}), **out}
