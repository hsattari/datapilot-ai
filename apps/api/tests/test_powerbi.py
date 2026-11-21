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
    base = [m["name"] for m in out["model_bim"]["model"]["tables"][0]["measures"]
            if not any(a["name"] == "datapilot_kind" for a in m["annotations"])]
    assert base == ["A"] and out["skipped"][0]["metric"] == "c"
    json.dumps(out)  # serialisable


def test_export_endpoint(client):
    h, _ = register(client, "Pbi", "p@p.test")
    ds, mapping = ingest_and_model(client, h)
    client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h)
    r = client.get("/api/v1/export/powerbi", headers=h).json()
    names = {m["name"] for m in r["model_bim"]["model"]["tables"][0]["measures"]}
    assert {"Net Revenue", "Orders"} <= names and "Net Revenue YTD" in names and "Gross Profit" not in names
    client.post("/api/v1/metrics/gross_profit/approve", headers=h)
    r = client.get("/api/v1/export/powerbi", headers=h).json()
    assert "Gross Profit = SUMX(FILTER(" in r["measures_dax"]
    assert client.get("/api/v1/export/powerbi").status_code == 401


def test_time_intelligence_dimensions_relationships_and_rls_template():
    out = build_export([dict(name="net_revenue", label="Net Revenue", description="d", version=1, expression="net_sales",
                             aggregation="sum", status="approved")])
    model = out["model_bim"]["model"]
    names = [t["name"] for t in model["tables"]]
    assert names == ["fact_sales", "dim_date", "dim_product", "dim_customer"]
    meas = {m["name"]: m["expression"] for m in model["tables"][0]["measures"]}
    assert meas["Net Revenue YTD"] == "TOTALYTD([Net Revenue], dim_date[Date])"
    assert meas["Net Revenue Prior Year"] == "CALCULATE([Net Revenue], SAMEPERIODLASTYEAR(dim_date[Date]))"
    assert meas["Net Revenue YoY %"].startswith("DIVIDE(")
    date = model["tables"][1]
    assert date["dataCategory"] == "Time" and any(c.get("isKey") for c in date["columns"])
    rel = {r["name"]: (r["fromColumn"], r["toTable"], r["toColumn"]) for r in model["relationships"]}
    assert rel["fact_date"] == ("order_date", "dim_date", "Date") and rel["fact_dim_product"] == ("product", "dim_product", "product_key")
    assert model["roles"][0]["tablePermissions"][0]["name"] == "fact_sales" and "REPLACE_WITH_REGION" in json.dumps(model["roles"])
    json.dumps(out)


def test_metric_scope_is_built_into_the_measure():
    out = build_export([dict(name="nr", label="NR", description="d", version=2, expression="net_sales", aggregation="sum",
                             status="approved", default_filters=[{"field": "region", "value": 'No"rth'}, {"field": "category", "value": "Tools"}],
                             effective_from="2026-01-01", effective_to="2026-05-31")])
    dax = out["model_bim"]["model"]["tables"][0]["measures"][0]["expression"]
    assert dax == ('CALCULATE(SUM(fact_sales[net_sales]), fact_sales[region] = "No""rth", dim_product[category] = "Tools", '
                   'fact_sales[order_date] >= DATE(2026, 1, 1), fact_sales[order_date] <= DATE(2026, 5, 31))')
