"""Polling ingestion worker. In production replace with a queue (Azure Service Bus / Celery)."""
import logging
import time

from .db import SessionLocal
from .config import get_settings
from .services import health, summaries
from .services.alerts import run_due_alerts
from .services.ingestion import run_pending
from .services.lifecycle import purge_expired

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")


def main(poll_seconds: float = 2.0) -> None:
    delay = poll_seconds
    last_purge = 0.0
    last_alerts = 0.0
    last_monitor = 0.0
    while True:
        try:
            with SessionLocal() as db:
                n = run_pending(db)
            delay = poll_seconds
            if n:
                log.info("processed %d job(s)", n)
            every = get_settings().alert_check_minutes
            if every > 0 and time.monotonic() - last_alerts > 60:  # look for due alerts once a minute
                with SessionLocal() as db:
                    checked = run_due_alerts(db, every)
                last_alerts = time.monotonic()
                if checked:
                    log.info("checked %d alert(s)", checked)
            if time.monotonic() - last_monitor > 900:  # refresh health and weekly summaries every 15 minutes
                with SessionLocal() as db:
                    new_issues = health.record_new_issues(db)
                    made = summaries.run_weekly(db)
                last_monitor = time.monotonic()
                if new_issues or made:
                    log.info("monitoring: %d new issue(s), %d summary(ies)", new_issues, made)
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
