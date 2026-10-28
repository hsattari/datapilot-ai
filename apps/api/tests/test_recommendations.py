"""Written recommendations: evidence, baseline, confidence and caveats, from governed numbers only."""
from .conftest import register
from .test_insights import steady, tenant


def recs(client, h):
    r = client.get("/api/v1/recommendations", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_fall_is_recommended_with_evidence_baseline_confidence_and_caveats(client):
    h = tenant(client, steady())
    out = recs(client, h)
    by_id = {r["id"]: r for r in out["recommendations"]}
    f = by_id["gross_profit:fall"]
    # May profit 110, June 50; Gadget accounts for all of the change
    assert f["baseline"]["period"] == "2026-05" and f["baseline"]["value"] == 110.0 and f["baseline"]["current_value"] == 50.0
    assert any("Product 'Gadget' accounts for 100%" in e for e in f["evidence"])
    assert any("Gadget" in s for s in f["next_steps"])
    assert f["confidence"]["level"] in ("low", "medium", "high") and f["confidence"]["reasons"]
    assert any("not why" in c for c in f["caveats"])
    assert all(q.lstrip().upper().startswith("SELECT") for q in f["sql"])
    assert out["recommendations"][0]["priority"] <= out["recommendations"][-1]["priority"]


def test_no_recommendation_when_nothing_moved_materially(client):
    rows = [(f"O{i}", f"2026-{mo:02d}-10", 100, 60, "Widget") for i, mo in enumerate(range(1, 8), 1)]
    h = tenant(client, rows, org="Flat", email="f@rec.test")
    assert [r for r in recs(client, h)["recommendations"] if r["kind"] in ("fall", "rise")] == []


def test_not_enough_history_is_reported_not_guessed(client):
    h = tenant(client, [("A", "2026-01-10", 100, 60, "W")], org="Short", email="s@rec.test")
    out = recs(client, h)
    assert out["recommendations"] == [] and out["skipped"]


def test_data_problem_comes_first_and_lowers_confidence(client):
    h = tenant(client, steady(), org="Prob", email="p@rec.test")
    from app.db import SessionLocal
    from app.services.ingestion import run_pending
    sid = client.get("/api/v1/data-sources", headers=h).json()[0]["id"]
    r = client.post(f"/api/v1/data-sources/{sid}/uploads", headers=h, files={"file": ("bad.csv", b"a,A\n1,2\n", "text/csv")})
    with SessionLocal() as db:
        run_pending(db)
    out = recs(client, h)["recommendations"]
    assert out[0]["kind"] == "data_problem" and "bad.csv" in " ".join(out[0]["evidence"])
    fall = next(x for x in out if x["id"] == "gross_profit:fall")
    assert any(x.startswith("- a data source has an open problem") for x in fall["confidence"]["reasons"])


def test_recommendations_are_per_organisation(client):
    h = tenant(client, steady(), org="One", email="o@rec.test")
    h2, _ = register(client, "Two", "t@rec.test")
    assert recs(client, h2)["recommendations"] == []
    assert recs(client, h)["recommendations"]
