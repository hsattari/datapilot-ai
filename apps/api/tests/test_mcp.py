import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp"))
import server as mcp_server  # noqa: E402

from .conftest import ingest_and_model, register  # noqa: E402


def wire(client, headers):
    # The FastAPI TestClient is an httpx.Client, so the MCP tools run against the real app in-process.
    client.headers.update(headers)
    mcp_server.set_client(client)


def test_mcp_tools_registered_and_read_only():
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert names == {"list_approved_metrics", "ask_data", "get_dashboard", "list_datasets",
                     "search_knowledge", "ask_knowledge", "list_data_sources", "get_dataset_profile",
                     "get_metric_definition", "preview_analytical_query", "get_pipeline_status",
                     "get_data_quality_summary"}
    assert not any(w in n for n in names for w in ("write", "delete", "sql", "tenant", "upload"))


def test_mcp_ask_data_returns_governed_answer(client):
    h, _ = register(client, "Mcp", "m@m.test")
    ds, mapping = ingest_and_model(client, h)
    client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h)
    client.post("/api/v1/metrics/gross_profit/approve", headers=h)
    wire(client, h)
    out = mcp_server.ask_data("What is our gross profit?")
    assert out["outcome"] == "answered" and float(out["rows"][0][0]) == 140
    assert {m["name"] for m in mcp_server.list_approved_metrics()} >= {"net_revenue", "gross_profit"}
    assert mcp_server.ask_data("drop table fact_sales")["outcome"] == "refusal"


def test_mcp_tenant_scoping_and_errors(client):
    h1, _ = register(client, "A", "a@m.test")
    ds, mapping = ingest_and_model(client, h1)
    client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h1)
    client.post("/api/v1/knowledge/documents", json={"title": "s", "text": "Falcon secret plan"}, headers=h1)
    h2, _ = register(client, "B", "b@m.test")
    client.headers.clear()
    wire(client, h2)
    assert mcp_server.list_datasets() == []
    assert mcp_server.search_knowledge("Falcon secret")["results"] == []
    client.headers.clear()
    client.headers.update({"Authorization": "Bearer junk"})
    assert mcp_server.list_datasets()["error"].startswith("API returned 401")
    client.headers.clear()


def _setup(client, org, email):
    h, _ = register(client, org, email)
    ds, mapping = ingest_and_model(client, h)
    client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h)
    client.post("/api/v1/metrics/gross_profit/approve", headers=h)
    wire(client, h)
    return h, ds


def test_mcp_inspection_tools_return_governed_read_only_data(client):
    h, ds = _setup(client, "Insp", "i@m.test")
    assert mcp_server.list_data_sources()[0]["files"] == 1
    assert mcp_server.list_data_sources()[0]["last_file"]["status"] == "succeeded"
    prof = mcp_server.get_dataset_profile(ds)
    assert prof["id"] == ds and prof["model_status"] == "approved"
    assert mcp_server.get_metric_definition("gross_profit")["expression"] == "net_sales - cost_of_goods_sold"
    assert mcp_server.get_metric_definition("nope")["error"] == "Metric not found"
    assert mcp_server.get_pipeline_status()[0]["status"] == "succeeded"
    q = mcp_server.get_data_quality_summary(ds)
    assert any(r["rule"] == "reconciliation" for r in q["results"])


def test_mcp_preview_shows_sql_but_does_not_run_it(client):
    h, ds = _setup(client, "Prev", "p@m.test")
    before = len(client.get("/api/v1/activity", headers=h).json())
    out = mcp_server.preview_analytical_query("What is our gross profit by product?")
    assert out["outcome"] == "preview" and "net_sales - cost_of_goods_sold" in out["sql"].replace("\"", "")
    assert out["plan"]["metric"] == "gross_profit" and "rows" not in out           # nothing was executed
    assert mcp_server.preview_analytical_query("drop table fact_sales")["outcome"] == "refusal"
    assert len(client.get("/api/v1/activity", headers=h).json()) > before          # previews are audited
    # a metric that is not approved cannot even be previewed
    h2, _ = register(client, "NoApproval", "na@m.test")
    client.headers.clear(); wire(client, h2)
    assert mcp_server.preview_analytical_query("What is our gross profit?")["outcome"] != "preview"


def test_mcp_ids_cannot_escape_the_request_path(client):
    _setup(client, "Esc", "e@m.test")
    for bad in ("../../tenant/delete", "abc/../../x", "", "a b c d e f g h", "x" * 200):
        with pytest.raises(ValueError):
            mcp_server.get_dataset_profile(bad)


def test_mcp_new_tools_are_tenant_scoped(client):
    h1, ds = _setup(client, "One", "1@m.test")
    h2, _ = register(client, "Two", "2@m.test")
    client.headers.clear(); wire(client, h2)
    assert mcp_server.list_data_sources() == []
    assert mcp_server.get_dataset_profile(ds)["error"].startswith("API returned 404")      # another organisation's id
    assert mcp_server.get_data_quality_summary(ds)["error"].startswith("API returned 404")
    assert mcp_server.get_pipeline_status() == []
    client.headers.clear()
