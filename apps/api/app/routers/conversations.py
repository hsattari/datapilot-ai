"""Conversations with follow-up context, answer feedback and permission-controlled export of an answer.

A conversation belongs to the person who started it: other people in the same organisation get 404, as do other
organisations. Follow-ups are read as changes to the previous plan by rules (see planner.followup_plan) and go through
the same checks as any question. An export re-runs the stored plan, so approval, permissions and current data are
re-checked at the moment of export; it is limited to administrators and analysts, rate-limited and logged.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from ..db import get_db
from ..deps import Principal, get_principal, require_admin, require_writer
from ..schemas import QuestionIn
from ..services import answering, ratelimit
from ..services.common import audit, run_plan
from ..services.lifecycle import table_csv
from ..services.query_engine import Plan, QueryRejected

router = APIRouter(prefix="/api/v1", tags=["conversations"])
MAX_MESSAGES = 100          # per conversation
ANSWER_ROWS_STORED = 50     # rows kept with a message for display later; an export re-runs the plan for all rows
EXPORT_LIMIT = ratelimit.by_user("export_answer", "rate_answer_export_per_minute")


class FeedbackIn(BaseModel):
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=500)


def _mine(db: Session, p: Principal, conversation_id: str) -> m.Conversation:
    c = db.get(m.Conversation, conversation_id)
    if c is None or c.tenant_id != p.tenant_id or c.user_id != p.user_id:
        raise HTTPException(404, "Not found")
    return c


def _my_message(db: Session, p: Principal, message_id: str) -> m.ConversationMessage:
    x = db.get(m.ConversationMessage, message_id)
    if x is None or x.tenant_id != p.tenant_id or x.user_id != p.user_id:
        raise HTTPException(404, "Not found")
    return x


def _message_json(x: m.ConversationMessage) -> dict:
    return {"id": x.id, "question": x.question, "outcome": x.outcome, "interpreted": x.interpreted,
            "answer": x.answer, "feedback": x.feedback, "created_at": x.created_at.isoformat()}


@router.post("/conversations", status_code=201)
def create_conversation(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    c = m.Conversation(tenant_id=p.tenant_id, user_id=p.user_id)
    db.add(c)
    db.commit()
    return {"id": c.id, "title": c.title}


@router.get("/conversations")
def list_conversations(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(m.ConversationMessage.conversation_id, func.count())
                             .where(m.ConversationMessage.tenant_id == p.tenant_id, m.ConversationMessage.user_id == p.user_id)
                             .group_by(m.ConversationMessage.conversation_id)).all())
    rows = db.scalars(select(m.Conversation).where(m.Conversation.tenant_id == p.tenant_id, m.Conversation.user_id == p.user_id)
                      .order_by(m.Conversation.created_at.desc()).limit(50)).all()
    return [{"id": c.id, "title": c.title, "messages": counts.get(c.id, 0), "created_at": c.created_at.isoformat()} for c in rows]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    c = _mine(db, p, conversation_id)
    msgs = db.scalars(select(m.ConversationMessage).where(m.ConversationMessage.conversation_id == c.id)
                      .order_by(m.ConversationMessage.created_at)).all()
    return {"id": c.id, "title": c.title, "messages": [_message_json(x) for x in msgs]}


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    c = _mine(db, p, conversation_id)
    for x in db.scalars(select(m.ConversationMessage).where(m.ConversationMessage.conversation_id == c.id)):
        db.delete(x)
    db.delete(c)
    audit(db, p, "conversation.deleted")
    db.commit()


def _previous_plan(msgs: list[m.ConversationMessage]) -> dict | None:
    for x in reversed(msgs):
        if x.outcome in ("answered", "explanation") and x.plan.get("metric"):
            return x.plan
    return None


@router.post("/conversations/{conversation_id}/messages", dependencies=[ratelimit.by_user("query", "rate_query_per_minute")])
def add_message(conversation_id: str, body: QuestionIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    c = _mine(db, p, conversation_id)
    msgs = db.scalars(select(m.ConversationMessage).where(m.ConversationMessage.conversation_id == c.id)
                      .order_by(m.ConversationMessage.created_at)).all()
    if len(msgs) >= MAX_MESSAGES:
        raise HTTPException(409, "This conversation is full. Start a new one.")
    payload = answering.answer_question(db, p, body.question, _previous_plan(msgs))
    outcome = payload.get("outcome", "clarification")
    if outcome == "explanation":
        plan = {"intent": "explain_change", "metric": (payload.get("metric") or {}).get("name"), "months": payload.get("n_months")}
    else:
        plan = payload.get("plan") or {}
    shown = jsonable_encoder(payload)
    if isinstance(shown.get("rows"), list) and len(shown["rows"]) > ANSWER_ROWS_STORED:
        shown["rows_total"], shown["rows"] = len(shown["rows"]), shown["rows"][:ANSWER_ROWS_STORED]
        shown["truncated"] = True
    msg = m.ConversationMessage(tenant_id=p.tenant_id, conversation_id=c.id, user_id=p.user_id,
                                question=body.question[:1000], outcome=outcome, plan=jsonable_encoder(plan),
                                interpreted=payload.get("interpreted"), answer=shown)
    db.add(msg)
    if not msgs:
        c.title = body.question[:60]
    db.commit()
    return {"message_id": msg.id, **payload}


@router.post("/messages/{message_id}/feedback")
def give_feedback(message_id: str, body: FeedbackIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    x = _my_message(db, p, message_id)
    x.feedback, x.feedback_comment = body.rating, (body.comment or None)
    audit(db, p, "answer.feedback", rating=body.rating)   # the comment text is not copied into the audit log
    db.commit()
    return {"rating": x.feedback}


@router.get("/messages/{message_id}/export.csv", dependencies=[EXPORT_LIMIT])
def export_answer(message_id: str, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    x = _my_message(db, p, message_id)
    if x.outcome != "answered" or not x.plan.get("metric"):
        raise HTTPException(409, "This answer has no table to export.")
    try:
        out = run_plan(db, p, Plan.model_validate(x.plan))   # approval, permissions and current data re-checked now
    except QueryRejected as e:
        raise HTTPException(403, str(e))
    except Exception:  # noqa: BLE001
        raise HTTPException(409, "This answer can no longer be run. Ask the question again.")
    audit(db, p, "answer.exported", message_id=x.id, rows=len(out["rows"]))
    db.commit()
    return Response(table_csv(out["columns"], out["rows"]), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="answer-{x.id[:8]}.csv"',
                             "Cache-Control": "no-store"})


@router.get("/feedback/summary")
def feedback_summary(p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    """AI quality signal for administrators: counts, and the questions marked not helpful (no person is named)."""
    rows = dict(db.execute(select(m.ConversationMessage.feedback, func.count()).where(
        m.ConversationMessage.tenant_id == p.tenant_id, m.ConversationMessage.feedback.is_not(None))
        .group_by(m.ConversationMessage.feedback)).all())
    down = db.scalars(select(m.ConversationMessage).where(
        m.ConversationMessage.tenant_id == p.tenant_id, m.ConversationMessage.feedback == "down")
        .order_by(m.ConversationMessage.created_at.desc()).limit(20)).all()
    return {"helpful": rows.get("up", 0), "not_helpful": rows.get("down", 0),
            "recent_not_helpful": [{"question": x.question[:200], "comment": x.feedback_comment,
                                    "outcome": x.outcome, "created_at": x.created_at.isoformat()} for x in down]}
