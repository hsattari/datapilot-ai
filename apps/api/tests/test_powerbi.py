import json

import pytest

from app.services.powerbi import NotExportable, build_export, measure_dax

from .conftest import ingest_and_model, register


def test_simple_measures():
    assert measure_dax("net_sales", "sum") == "SUM(fact_sales[net_sales])"
    assert measure_dax("order_id", "count_distinct") == "DISTINCTCOUNTNOBLANK(fact_sales[order_id])"
    assert measure_dax("net_sales", "avg") == "AVERAGE(fact_sales[net_sales])"


def test_multi_column_measure_reproduces_sql_null_semantics():
    dax = measure_dax("net_sales - cost_of_goods_sold", "sum")
    # SQL SUM(a - b) skips rows where a or b is NULL; DAX would treat BLANK as 0, so the filter is essential.
    assert dax == ("SUMX(FILTER(fact_sales, NOT ISBLANK(fact_sales[cost_of_goods_sold]) && "
                   "NOT ISBLANK(fact_sales[net_sales])), fact_sales[net_sales] - fact_sales[cost_of_goods_sold])")


@pytest.mark.parametrize("expr,agg", [("net_sales / cost_of_goods_sold", "sum"), ("salary", "sum"),
                                      ("net_sales", "median"), ("1 + 2", "sum"), ("net_sales + cost_of_goods_sold", "count_distinct")])
def test_unsupported_rejected(expr, agg):
    with pytest.raises(Exception):
        measure_dax(expr, agg)


def test_export_only_approved_and_skips_bad():
    base = dict(description="d", version=1)
    out = build_export([
        dict(name="a", label="A", expression="net_sales", aggregation="sum", status="approved", **base),
        dict(name="b", label="B", expression="net_sales - cost_of_goods_sold", aggregation="sum",
             status="requires_business_approval", **base),
        dict(name="c", label="C", expression="net_sales / cost_of_goods_sold", aggregation="sum",
             status="approved", **base)])
    names = [m["name"] for m in out["model_bim"]["model"]["tables"][0]["measures"]]
    assert names == ["A"] and out["skipped"][0]["metric"] == "c"
    json.dumps(out)  # serialisable


def test_export_endpoint(client):
    h, _ = register(client, "Pbi", "p@p.test")
    ds, mapping = ingest_and_model(client, h)
    client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h)
    r = client.get("/api/v1/export/powerbi", headers=h).json()
    assert {m["name"] for m in r["model_bim"]["model"]["tables"][0]["measures"]} == {"Net Revenue", "Orders"}
    client.post("/api/v1/metrics/gross_profit/approve", headers=h)
    r = client.get("/api/v1/export/powerbi", headers=h).json()
    assert "Gross Profit = SUMX(FILTER(" in r["measures_dax"]
    assert client.get("/api/v1/export/powerbi").status_code == 401
