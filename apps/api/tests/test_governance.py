"""Metric scope (default filters, effective dates), field classification, onboarding/consent, AI token budget,
product analytics."""
import json
from types import SimpleNamespace

import pytest

from app import models as m
from app.config import get_settings
from app.db import SessionLocal
from app.services import aibudget, planner

from .conftest import register
from .test_insights import steady
from .test_starters import auto_tenant, upload
from .test_workspace import add_viewer


def ask(client, h, q):
    return client.post("/api/v1/analytics/query", json={"question": q}, headers=h).json()


def total(client, h, q="total revenue"):
    r = ask(client, h, q)
    assert r["outcome"] == "answer" or "rows" in r, r
    return float(r["rows"][0][0]), r


def metric(client, h, name="net_revenue"):
    return next(x for x in client.get("/api/v1/metrics", headers=h).json() if x["name"] == name)


# ---------------------------------------------------------------- metric scope
def test_scope_needs_approval_and_changes_the_numbers_with_a_caveat(client):
    h, _ = auto_tenant(client, "s@scope.test", "Scope")
    upload(client, h, steady())
    assert total(client, h)[0] == 2200
    v = metric(client, h)["version"]
    r = client.put("/api/v1/metrics/net_revenue/scope", headers=h, json={"default_filters": [{"field": "product", "value": "Widget"}]})
    assert r.status_code == 200 and r.json()["status"] == "requires_business_approval" and r.json()["version"] == v + 1
    assert ask(client, h, "total revenue").get("rows") is None            # not approved: no answer on the new definition
    assert client.post("/api/v1/metrics/net_revenue/approve", headers=h).status_code == 200
    val, res = total(client, h)
    assert val == 1100
    assert any("only covers product = Widget" in c for c in res["caveats"])
    assert res["metric"]["scope"]["filters"] == [{"field": "product", "value": "Widget"}]
    # the default filter cannot be overridden by a person's own filter: Widget AND Gadget is empty
    from app.services.query_engine import Plan, Filter, compile_plan
    sql, params = compile_plan(Plan(intent="metric_total", metric="net_revenue", filters=[Filter(field="product", value="Gadget")]),
                               "net_sales", "sum", scope={"filters": [{"field": "product", "value": "Widget"}]})
    assert sql.count("product = ?") == 2 and params == ["Gadget", "Widget"]


def test_effective_dates_and_validation(client):
    h, tid = auto_tenant(client, "d@scope.test", "ScopeD")
    upload(client, h, steady())
    assert client.put("/api/v1/metrics/net_revenue/scope", headers=h,
                      json={"effective_from": "2026-03-01", "effective_to": "2026-02-01"}).status_code == 422
    assert client.put("/api/v1/metrics/net_revenue/scope", headers=h, json={"default_filters": [{"field": "salary", "value": "x"}]}).status_code == 422
    assert client.put("/api/v1/metrics/net_revenue/scope", headers=h,
                      json={"effective_from": "2026-01-01", "effective_to": "2026-05-31"}).status_code == 200
    client.post("/api/v1/metrics/net_revenue/approve", headers=h)
    val, res = total(client, h)
    assert val == 1500 and any("2026-01-01 to 2026-05-31" in c for c in res["caveats"])
    before = metric(client, h)["version"]
    client.put("/api/v1/metrics/net_revenue/scope", headers=h, json={"effective_from": "2026-01-01", "effective_to": "2026-05-31"})
    assert metric(client, h)["version"] == before                           # nothing changed: no new version
    assert client.put("/api/v1/metrics/net_revenue/scope", headers=add_viewer(tid, "a@scope.test", "analyst"), json={}).status_code == 403


# ---------------------------------------------------------------- classification
def test_restricted_field_and_metric_follow_the_role(client):
    h, tid = auto_tenant(client, "c@cls.test", "Cls")
    upload(client, h, steady())
    r = client.put("/api/v1/classification", headers=h, json={"fields": {"product": "restricted", "region": "public"}, "metrics": {"gross_profit": "confidential"}})
    assert r.status_code == 200 and r.json() == {"fields": {"product": "restricted"}, "metrics": {"gross_profit": "confidential"}}
    viewer, analyst = add_viewer(tid, "v@cls.test", "viewer"), add_viewer(tid, "an@cls.test", "analyst")
    q = "net revenue by product"
    assert "restricted" in ask(client, viewer, q)["message"] and "restricted" in ask(client, analyst, q)["message"]
    assert "rows" in ask(client, h, q)                                       # admin may
    assert "confidential" in ask(client, viewer, "gross profit")["message"]
    assert "rows" in ask(client, analyst, "gross profit")                    # analyst may see confidential
    assert "rows" in ask(client, viewer, "net revenue by month")             # unclassified is open
    assert client.put("/api/v1/classification", headers=viewer, json={}).status_code == 403
    assert client.put("/api/v1/classification", headers=h, json={"fields": {"salary": "restricted"}}).status_code == 422
    assert client.put("/api/v1/classification", headers=h, json={"fields": {"product": "secret"}}).status_code == 422


# ---------------------------------------------------------------- onboarding and consent
def test_business_description_and_consent(client, monkeypatch):
    r = client.post("/api/v1/tenants", json={"organisation": "Onb", "email": "o@onb.test", "password": "correct-horse-1",
                                              "business_description": "  Online shop selling garden tools  ", "accept_terms": True})
    assert r.status_code == 201
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/api/v1/settings", headers=h).json()["business_description"] == "Online shop selling garden tools"
    c = client.get("/api/v1/consent", headers=h).json()
    assert c["up_to_date"] and c["accepted_version"] == get_settings().terms_version and c["accepted_at"]
    client.patch("/api/v1/settings", headers=h, json={"business_description": "Updated text"})
    events = [e for e in client.get("/api/v1/activity", headers=h).json() if e["action"] == "settings.updated"]
    assert events and "Updated text" not in json.dumps(events)             # description is never copied into the audit trail
    monkeypatch.setattr(get_settings(), "require_consent", True)
    body = {"organisation": "Onb2", "email": "o2@onb.test", "password": "correct-horse-1"}
    assert client.post("/api/v1/tenants", json=body).status_code == 422
    r2 = client.post("/api/v1/tenants", json={**body, "accept_terms": True})
    assert r2.status_code == 201
    monkeypatch.setattr(get_settings(), "terms_version", "2027-01")
    h2 = {"Authorization": f"Bearer {r2.json()['token']}"}
    assert client.get("/api/v1/consent", headers=h2).json()["up_to_date"] is False
    assert client.post("/api/v1/consent", headers=h2).json()["accepted_version"] == "2027-01"
    assert client.get("/api/v1/consent", headers=h2).json()["up_to_date"] is True


# ---------------------------------------------------------------- AI token budget
class _Fake:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.reply)))],
                               usage=SimpleNamespace(total_tokens=1000))


def test_tokens_are_counted_and_an_exhausted_budget_falls_back_to_rules(client, monkeypatch):
    s = get_settings()
    for k, v in dict(azure_openai_endpoint="https://example.invalid", azure_openai_api_key="k", azure_openai_deployment="d").items():
        monkeypatch.setattr(s, k, v)
    fake = _Fake({"intent": "metric_total", "metric": "net_revenue", "dimensions": [], "time_range": {"type": "all_time"},
                  "filters": [], "visualization": "number"})
    monkeypatch.setattr(planner, "_client", lambda: fake)
    h, tid = auto_tenant(client, "b@bud.test", "Bud")
    upload(client, h, steady())
    base = fake.calls                                                       # starter content also asks the model
    assert "rows" in ask(client, h, "how is the business doing")           # needs the model (rules would not match)
    assert fake.calls > base
    with SessionLocal() as db:
        assert aibudget.used(db, tid) == 1000 * fake.calls
    assert client.get("/api/v1/usage", headers=h).json()["quota"]["used"]["ai_tokens"] == 1000 * fake.calls
    with SessionLocal() as db:                                              # use up the budget (starter plan: 300,000 a month)
        db.get(m.Tenant, tid).plan = "starter"
        db.add(m.AiUsage(tenant_id=tid, month=aibudget.month_key(), kind="plan2", tokens=10_000_000, calls=1))
        db.commit()
    seen = fake.calls
    r = ask(client, h, "total revenue")                                     # rules can still answer this one
    assert fake.calls == seen and "rows" in r
    assert aibudget.exhausted(tid)


# ---------------------------------------------------------------- product analytics
def test_product_analytics_counts_activation_and_people(client):
    h, tid = auto_tenant(client, "p@pa.test", "Pa")
    first = client.get("/api/v1/analytics/product", headers=h).json()
    assert first["activated"] is False and first["activation_steps"]["uploaded_a_file"] is False
    upload(client, h, steady())
    ask(client, h, "total revenue")
    r = client.get("/api/v1/analytics/product", headers=h).json()
    assert r["activated"] is True and r["minutes_to_first_dashboard"] is not None and r["minutes_to_first_answer"] is not None
    assert r["active_people_7d"] == 1 and len(r["weekly_active_people"]) == 8 and r["weekly_active_people"][-1]["active_people"] == 1
    assert r["cost_to_serve_30d"]["questions"] >= 1 and r["cost_to_serve_30d"]["rows_loaded"] > 0
    assert client.get("/api/v1/analytics/product", headers=add_viewer(tid, "v@pa.test")).status_code == 403
