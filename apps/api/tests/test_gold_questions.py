"""Gold-question regression gate (run in CI before release)."""
import json
from pathlib import Path

import pytest

from .conftest import ingest_and_model, register

GOLD = json.loads((Path(__file__).resolve().parents[3] / "tests/ai_evaluation/gold_questions.json").read_text())


@pytest.mark.parametrize("case", GOLD, ids=[c["id"] for c in GOLD])
def test_gold(client, case):
    h, _ = register(client, "Gold", f"{case['id']}@gold.test")
    ds, mapping = ingest_and_model(client, h)
    client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h)
    for name in case.get("approve", []):
        assert client.post(f"/api/v1/metrics/{name}/approve", headers=h).status_code == 200
    out = client.post("/api/v1/analytics/query", json={"question": case["question"]}, headers=h).json()
    assert out["outcome"] == case["expect"], out
    if "value" in case:
        assert float(out["rows"][0][0]) == case["value"]
        assert out["metric"]["definition"] and "latest_order_date" in out["freshness"]
    if "rows" in case:
        assert len(out["rows"]) == case["rows"]
