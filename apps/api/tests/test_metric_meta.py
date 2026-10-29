"""Metric metadata: owner, unit, example questions; administrators only; definition and approval untouched."""
from .conftest import register
from .test_workspace import add_viewer


def metric(client, h, name="net_revenue"):
    return next(x for x in client.get("/api/v1/metrics", headers=h).json() if x["name"] == name)


def test_admin_sets_metadata_without_touching_definition_or_approval(client):
    h, _ = register(client, "Meta", "m@meta.test")
    before = metric(client, h)
    r = client.patch("/api/v1/metrics/net_revenue", headers=h,
                     json={"owner": " Finance ", "unit": "currency", "example_questions": ["What was net revenue last month?", "  "]})
    assert r.status_code == 200, r.text
    after = metric(client, h)
    assert after["owner"] == "Finance" and after["unit"] == "currency" and after["example_questions"] == ["What was net revenue last month?"]
    for k in ("expression", "version", "status", "aggregation"):
        assert after[k] == before[k]


def test_invalid_input_and_permissions(client):
    h, tid = register(client, "Meta2", "m2@meta.test")
    assert client.patch("/api/v1/metrics/net_revenue", headers=h, json={"unit": "furlongs"}).status_code == 422
    assert client.patch("/api/v1/metrics/net_revenue", headers=h, json={"example_questions": ["abc"] * 6}).status_code == 422
    assert client.patch("/api/v1/metrics/nope", headers=h, json={}).status_code == 404
    analyst = add_viewer(tid, "a@meta.test", "analyst")
    assert client.patch("/api/v1/metrics/net_revenue", headers=analyst, json={"owner": "x"}).status_code == 403
    h2, _ = register(client, "Meta3", "m3@meta.test")
    client.patch("/api/v1/metrics/net_revenue", headers=h, json={"owner": "Mine"})
    assert metric(client, h2)["owner"] is None            # other organisations never see it
