"""Lineage reaches dashboards, and shows a metric's scope."""
from .test_starters import auto_tenant, upload
from .test_insights import steady


def test_dashboard_is_in_the_graph_and_the_metric_trace(client):
    h, _ = auto_tenant(client, "l@ld.test", "Ld")
    upload(client, h, steady())
    g = client.get("/api/v1/lineage", headers=h).json()
    dash = [n for n in g["nodes"] if n["kind"] == "dashboard"]
    assert [n["label"] for n in dash] == ["Overview"]
    assert any(e["to"] == dash[0]["id"] or e.get("target") == dash[0]["id"] for e in g["edges"])
    t = client.get("/api/v1/lineage/metrics/net_revenue", headers=h).json()
    assert [d["title"] for d in t["used_by"]["dashboards"]] == ["Overview"]
    assert t["metric"]["scope"] == {"default_filters": [], "effective_from": None, "effective_to": None}
