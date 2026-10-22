# Operations notes

- Job states: queued → running → succeeded | failed. Data errors fail once (no retry); transient errors
  retry up to 3 times. The worker loop backs off exponentially on loop errors.
- Failed ingestion never publishes partial data: the model is built only on explicit approval, and the
  load reconciles loaded + rejected = staged.
- Migrations: `alembic upgrade head`. Rollback: `alembic downgrade -1`.
- Observability: one JSON log line per request (request id, route template, status, duration, tenant/user ids; never
  question text, file names, e-mail, tokens or query strings) and `X-Request-ID` on every response. Traces go to
  Application Insights when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set (`OTEL_CONSOLE=true` prints spans while
  developing); spans pass through a scrubber that drops query strings and exception messages. Not yet verified
  against a real Application Insights resource.
- To do: Azure Monitor alert rules, backup/restore drills, cost quotas.
