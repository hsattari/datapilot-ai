"""Request IDs, structured logs and OpenTelemetry tracing.

Privacy rules (tested): logs and spans never contain question text, SQL parameters, file names, e-mail addresses,
passwords, tokens or query strings. They carry opaque identifiers (request id, tenant id, user id), route templates,
status codes, durations and counts. Exception *types* are recorded, not messages (messages can quote data).
"""
import contextvars
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from .config import get_settings

tracer = trace.get_tracer("datapilot")
log = logging.getLogger("datapilot.request")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
# Mutable per-request context: a dict so values set inside threadpool dependencies are visible to the middleware.
request_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("request_ctx", default={})
root_span: contextvars.ContextVar = contextvars.ContextVar("root_span", default=None)


def bind(**values) -> None:
    """Attach tenant/user ids to the current request's logs and root span."""
    ctx = request_ctx.get()
    ctx.update(values)
    for span in (root_span.get(), trace.get_current_span()):  # the request's root span, and the span we are in
        if span is not None and span.is_recording():
            for k, v in values.items():
                span.set_attribute(f"dp.{k}", str(v))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
             "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        d.update(getattr(record, "dp", {}))
        if record.exc_info:
            d["exc_type"] = record.exc_info[0].__name__  # type only: messages and stack locals can contain data
        return json.dumps(d, default=str)


def configure_logging() -> None:
    s = get_settings()
    root = logging.getLogger()
    if not any(getattr(h, "_datapilot", False) for h in root.handlers):
        h = logging.StreamHandler()
        h._datapilot = True
        root.addHandler(h)
    for h in root.handlers:
        if getattr(h, "_datapilot", False):
            h.setFormatter(JsonFormatter() if s.log_format == "json" else
                           logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").disabled = True  # it prints query strings; we log our own request line


def span_error(span, exc: BaseException) -> None:
    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
    span.set_attribute("error.type", type(exc).__name__)


_DROP_ATTRS = {"url.query"}
_STRIP_QUERY_ATTRS = {"http.target", "http.url", "url.full", "http.path"}
_DROP_EVENT_ATTRS = {"exception.message", "exception.stacktrace"}


def scrub_span(span):
    """Copy of a finished span without query strings and exception messages/stack traces (they can quote data)."""
    from opentelemetry.sdk.trace import Event, ReadableSpan

    attrs = {}
    for k, v in (span.attributes or {}).items():
        if k in _DROP_ATTRS:
            continue
        attrs[k] = v.split("?", 1)[0] if k in _STRIP_QUERY_ATTRS and isinstance(v, str) else v
    events = [Event(e.name, {k: v for k, v in (e.attributes or {}).items() if k not in _DROP_EVENT_ATTRS},
                    e.timestamp) for e in span.events]
    desc = span.status.description
    status = Status(span.status.status_code, desc.split(":", 1)[0] if desc else desc)  # keep the type, drop the message
    return ReadableSpan(name=span.name, context=span.context, parent=span.parent, resource=span.resource,
                        attributes=attrs, events=events, links=span.links, kind=span.kind,
                        status=status,
                        start_time=span.start_time, end_time=span.end_time,
                        instrumentation_scope=span.instrumentation_scope)


class ScrubbingExporter:
    """Wraps any span exporter so nothing leaves the process without being scrubbed."""

    def __init__(self, inner):
        self.inner = inner

    def export(self, spans):
        return self.inner.export([scrub_span(s) for s in spans])

    def shutdown(self):
        return self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        return self.inner.force_flush(timeout_millis) if hasattr(self.inner, "force_flush") else True


def build_exporters() -> list:
    s = get_settings()
    out = []
    if s.applicationinsights_connection_string:
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
        out.append(AzureMonitorTraceExporter(connection_string=s.applicationinsights_connection_string))
    if s.otel_console:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
        out.append(ConsoleSpanExporter())
    return out


def configure_tracing(app) -> None:
    """Install the tracer provider and instrument FastAPI, only when an exporter is configured."""
    exporters = build_exporters()
    if not exporters:
        return
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create(
        {"service.name": "datapilot-api", "deployment.environment": get_settings().environment}))
    for e in exporters:
        provider.add_span_processor(BatchSpanProcessor(ScrubbingExporter(e)))
    trace.set_tracer_provider(provider)
    instrument(app, provider)


def instrument(app, provider=None) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls="healthz")


def install(app) -> None:
    configure_logging()
    configure_tracing(app)

    @app.middleware("http")
    async def request_context(request, call_next):
        inbound = request.headers.get("x-request-id", "")
        rid = inbound if _SAFE_ID.match(inbound) else uuid.uuid4().hex  # never echo attacker-controlled text
        ctx = {"request_id": rid}
        token = request_ctx.set(ctx)
        root_token = root_span.set(trace.get_current_span())
        start = time.perf_counter()
        status = 500
        try:
            resp = await call_next(request)
            status = resp.status_code
            resp.headers["X-Request-ID"] = rid
            return resp
        finally:
            route = request.scope.get("route")
            if request.url.path != "/healthz":
                log.info("request", extra={"dp": {
                    "request_id": rid, "method": request.method,
                    "route": getattr(route, "path", "unmatched"),  # template, e.g. /api/v1/jobs/{job_id}: no ids, no query
                    "status": status, "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    **{k: v for k, v in ctx.items() if k != "request_id"}}})
            request_ctx.reset(token)
            root_span.reset(root_token)
