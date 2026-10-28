"""URL connector: connect a data source to a public https address, fetch now, or on a schedule."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import models as m
from ..config import get_settings
from ..db import get_db
from ..deps import Principal, require_writer
from ..services import connectors, netguard, ratelimit
from ..services.common import audit, owned

router = APIRouter(prefix="/api/v1")


class ConnectionIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    sync_every_hours: int | None = Field(default=None, ge=1, le=24 * 31)   # empty = only when you press Fetch now


@router.put("/data-sources/{source_id}/connection")
def set_connection(source_id: str, body: ConnectionIn, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    s = owned(db, m.DataSource, source_id, p.tenant_id)
    cfg = get_settings()
    try:   # fail early with the reason; the same checks run again on every fetch
        _, host, port = netguard.check_url(body.url.strip(), cfg.connector_allow_http)
        netguard.check_host(host, port)
    except netguard.UrlRejected as e:
        audit(db, p, "source.connection_rejected", source_id=s.id, address=connectors.redact(body.url.strip()), reason=str(e))
        db.commit()
        raise HTTPException(422, str(e)) from None
    s.url, s.sync_every_hours, s.kind = body.url.strip(), body.sync_every_hours, "url"
    s.last_sync_error = None
    audit(db, p, "source.connected", source_id=s.id, address=connectors.redact(s.url), sync_every_hours=s.sync_every_hours)
    db.commit()
    return {"id": s.id, "url": s.url, "sync_every_hours": s.sync_every_hours}


@router.get("/data-sources/{source_id}/connection")
def get_connection(source_id: str, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    s = owned(db, m.DataSource, source_id, p.tenant_id)
    return {"id": s.id, "url": s.url, "sync_every_hours": s.sync_every_hours,
            "last_sync_at": s.last_sync_at.isoformat() if s.last_sync_at else None, "last_sync_error": s.last_sync_error}


@router.delete("/data-sources/{source_id}/connection", status_code=204)
def remove_connection(source_id: str, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    s = owned(db, m.DataSource, source_id, p.tenant_id)
    s.url = s.sync_every_hours = s.last_sync_at = s.last_sync_error = None
    s.kind = "file"
    audit(db, p, "source.disconnected", source_id=s.id)
    db.commit()


@router.post("/data-sources/{source_id}/sync", status_code=202,
             dependencies=[ratelimit.by_user("sync", "rate_sync_per_minute")])
async def sync_now(source_id: str, p: Principal = Depends(require_writer), db: Session = Depends(get_db)):
    s = owned(db, m.DataSource, source_id, p.tenant_id)
    if not s.url:
        raise HTTPException(409, "This data source has no web address. Connect one first.")
    try:
        return await run_in_threadpool(connectors.sync, db, s)
    except connectors.SyncFailed as e:
        raise HTTPException(422, str(e)) from None
