# Operations notes

- Job states: queued → running → succeeded | failed. Data errors fail once (no retry); transient errors
  retry up to 3 times. The worker loop backs off exponentially on loop errors.
- Failed ingestion never publishes partial data: the model is built only on explicit approval, and the
  load reconciles loaded + rejected = staged.
- Migrations: `alembic upgrade head`. Rollback: `alembic downgrade -1`.
- To do: OpenTelemetry/Application Insights wiring, alerts, backup/restore drills, cost quotas.
