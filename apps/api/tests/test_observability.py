"""Request IDs, structured logs and tracing: behaviour and privacy."""
import json
import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import observability
from app.config import get_settings
from app.main import app

from .conftest import DATA, register
from .test_dimensions import setup_sales


@pytest.fixture(scope="module")
def exporter():
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(observability.ScrubbingExporter(exp)))  # same pipeline as production
    trace.set_tracer_provider(provider)  # once per process; the module-level tracer binds to it
    observability.instrument(app, provider)
    yield exp
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.uninstrument_app(app)


@pytest.fixture()
def spans(exporter):
    exporter.clear()
    return exporter


def log_lines(caplog):
    fmt = observability.JsonFormatter()
    return [json.loads(fmt.format(r)) for r in caplog.records if r.name == "datapilot.request"]


def test_request_id_is_generated_and_echoed_only_when_safe(client):
    r = client.get("/api/v1/auth/oidc/config")
    rid = r.headers["x-request-id"]
    assert len(rid) == 32 and rid.isalnum()
    assert client.get("/api/v1/auth/oidc/config", headers={"X-Request-ID": "trace-abc-12345"}).headers["x-request-id"] == "trace-abc-12345"
    for evil in ("short", "x" * 200, "has space in it", "inject<script>alert(1)</script>"):
        assert client.get("/api/v1/auth/oidc/config", headers={"X-Request-ID": evil}).headers["x-request-id"] != evil


def test_one_json_log_line_per_request_with_route_template_and_tenant(client, caplog):
    h, t = register(client, "Log", "o@log.test")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="datapilot.request"):
        client.get("/api/v1/activity?limit=3", headers=h)
        client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000", headers=h)
        client.get("/healthz")
    lines = log_lines(caplog)
    assert [x["route"] for x in lines] == ["/api/v1/activity", "/api/v1/jobs/{job_id}"]  # no query string, no raw ids, no healthz
    assert [x["status"] for x in lines] == [200, 404]
    assert all(x["tenant_id"] == t and x["duration_ms"] >= 0 and len(x["request_id"]) >= 8 for x in lines)
    assert all(x["method"] == "GET" for x in lines)


def test_unauthenticated_request_is_logged_without_tenant(client, caplog):
    with caplog.at_level(logging.INFO, logger="datapilot.request"):
        client.get("/api/v1/jobs")
    (line,) = log_lines(caplog)
    assert line["status"] == 401 and "tenant_id" not in line


def test_spans_for_requests_queries_and_ingestion(client, spans):
    h, t = register(client, "Spans", "o@spans.test")
    setup_sales(client, h)
    spans.clear()
    assert client.post("/api/v1/analytics/query", json={"question": "What is our gross profit?"}, headers=h).json()["outcome"] == "answered"
    by_name = {}
    for s in spans.get_finished_spans():
        by_name.setdefault(s.name, []).append(s)
    run = by_name["analytics.run_plan"][0]
    assert run.attributes["dp.metric"] == "gross_profit" and run.attributes["dp.rows"] == 1 and run.attributes["dp.tenant_id"] == t
    root = next(s for n, ss in by_name.items() if "/api/v1/analytics/query" in n for s in ss)
    assert root.attributes["dp.tenant_id"] == t  # bound from the authenticated principal
    assert not any("healthz" in n for n in by_name)

    spans.clear()
    from .test_dimensions import upload
    upload(client, h, "products.csv")
    ing = [s for s in spans.get_finished_spans() if s.name == "ingestion.job"][0]
    assert ing.attributes["dp.status"] == "succeeded" and ing.attributes["dp.rows_loaded"] == 2


def test_failed_query_span_records_error_type_not_message(client, spans):
    h, _ = register(client, "Err", "o@err.test")
    setup_sales(client, h)
    spans.clear()
    client.post("/api/v1/analytics/query", json={"question": "net revenue by category"}, headers=h)  # no products list yet
    run = next(s for s in spans.get_finished_spans() if s.name == "analytics.run_plan")
    assert run.status.status_code == trace.StatusCode.ERROR
    assert run.attributes["error.type"] == "QueryRejected"
    assert "products" not in (run.status.description or "").lower()  # the human message is not copied into telemetry


def test_no_sensitive_values_in_logs_or_spans(client, spans, caplog):
    email, pw = "secret.person@leak.test", "correct-horse-1"
    r = client.post("/api/v1/tenants", json={"organisation": "Leak Ltd", "email": email, "password": pw})
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-1"})
    with caplog.at_level(logging.INFO):
        setup_sales(client, h)
        client.post("/api/v1/analytics/query", json={"question": "What is our gross profit? SECRETQUESTION"}, headers=h)
        client.get("/api/v1/activity?limit=7", headers=h)
        client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-2"})
    blob = json.dumps([dict(s.attributes) | {"name": s.name, "status": str(s.status.description)} for s in spans.get_finished_spans()], default=str)
    blob += "\n".join(observability.JsonFormatter().format(r) for r in caplog.records if r.name.startswith("datapilot"))  # app loggers (the test client logs its own URLs)
    for secret in (email, pw, "wrong-password", token, "Bearer ", "SECRETQUESTION", "What is our", "orders_reference.csv",
                   "net_sales -", "SELECT", "limit=7"):
        assert secret not in blob, f"leaked into telemetry: {secret!r}"


def test_exporters_only_when_configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "applicationinsights_connection_string", "")
    monkeypatch.setattr(s, "otel_console", False)
    assert observability.build_exporters() == []
    monkeypatch.setattr(s, "applicationinsights_connection_string",
                        "InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=https://ingest.example.test/")
    (e,) = observability.build_exporters()
    assert type(e).__name__ == "AzureMonitorTraceExporter"


def test_json_formatter_never_prints_exception_messages():
    try:
        raise ValueError("customer secret@x.test leaked in message")
    except ValueError:
        import sys
        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    out = observability.JsonFormatter().format(rec)
    assert json.loads(out)["exc_type"] == "ValueError" and "secret@x.test" not in out


def test_scrubber_removes_query_strings_and_exception_messages():
    from opentelemetry.sdk.trace import TracerProvider as TP
    exp = InMemorySpanExporter()
    prov = TP()
    prov.add_span_processor(SimpleSpanProcessor(observability.ScrubbingExporter(exp)))
    t = prov.get_tracer("t")
    with pytest.raises(RuntimeError):
        with t.start_as_current_span("op", attributes={"url.query": "q=secret", "http.target": "/x?token=abc", "url.path": "/x",
                                                       "dp.rows": 3}):
            raise RuntimeError("customer a@b.test in message")  # default tracer behaviour records message + stack trace
    (s,) = exp.get_finished_spans()
    assert "url.query" not in s.attributes and s.attributes["http.target"] == "/x" and s.attributes["dp.rows"] == 3
    ev = [e for e in s.events if e.name == "exception"][0]
    assert ev.attributes["exception.type"] == "RuntimeError"
    assert "exception.message" not in ev.attributes and "exception.stacktrace" not in ev.attributes
    assert "a@b.test" not in (s.status.description or "")
