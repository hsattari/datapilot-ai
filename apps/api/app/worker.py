"""Polling ingestion worker. In production replace with a queue (Azure Service Bus / Celery)."""
import logging
import time

from .db import SessionLocal
from .services.ingestion import run_pending

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")


def main(poll_seconds: float = 2.0) -> None:
    delay = poll_seconds
    while True:
        try:
            with SessionLocal() as db:
                n = run_pending(db)
            delay = poll_seconds
            if n:
                log.info("processed %d job(s)", n)
        except Exception:  # keep the worker alive; back off exponentially
            log.exception("worker loop error")
            delay = min(delay * 2, 60)
        time.sleep(delay)


if __name__ == "__main__":
    main()
