"""Polling ingestion worker. In production replace with a queue (Azure Service Bus / Celery)."""
import logging
import time

from .db import SessionLocal
from .services.ingestion import run_pending
from .services.lifecycle import purge_expired

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")


def main(poll_seconds: float = 2.0) -> None:
    delay = poll_seconds
    last_purge = 0.0
    while True:
        try:
            with SessionLocal() as db:
                n = run_pending(db)
            delay = poll_seconds
            if n:
                log.info("processed %d job(s)", n)
            if time.monotonic() - last_purge > 3600:  # retention: at most hourly
                with SessionLocal() as db:
                    purged = purge_expired(db)
                last_purge = time.monotonic()
                if any(purged.values()):
                    log.info("retention purge: %s", purged)
        except Exception:  # keep the worker alive; back off exponentially
            log.exception("worker loop error")
            delay = min(delay * 2, 60)
        time.sleep(delay)


if __name__ == "__main__":
    main()
