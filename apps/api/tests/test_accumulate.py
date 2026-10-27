"""Sales files accumulate (upsert by order ID, latest file wins) instead of each upload replacing the last."""
from app.db import SessionLocal
from app.services.ingestion import run_pending

from .conftest import register


def csv(rows):
    return ("order_id,order_date,net_sales,cost,product\n" + "\n".join(",".join(map(str, r)) for r in rows) + "\n").encode()


def add_file(client, h, name, rows, approve=True, replace_existing=False):
    sid = client.post("/api/v1/data-sources", json={"name": name}, headers=h).json()["id"]
    r = client.post(f"/api/v1/data-sources/{sid}/uploads", headers=h, files={"file": (f"{name}.csv", csv(rows), "text/csv")})
    assert r.status_code == 202, r.text
    with SessionLocal() as db:
        run_pending(db)
    ds = next(d for d in client.get("/api/v1/datasets", headers=h).json() if d["name"] == name)
    if not approve:
        return ds["id"], None
    prof = client.get(f"/api/v1/datasets/{ds['id']}/profile", headers=h).json()["profile"]
    mapping = {k: v["column"] for k, v in prof["suggested_mapping"].items()}
    r = client.post(f"/api/v1/datasets/{ds['id']}/approve", json={"mapping": mapping, "replace_existing": replace_existing}, headers=h)
    assert r.status_code == 200, r.text
    return ds["id"], r.json()


def total(client, h, q="total revenue"):
    r = client.post("/api/v1/analytics/query", json={"question": q}, headers=h).json()
    assert r["outcome"] == "answered", r
    return float(r["rows"][0][0])


def setup(client, org="Acc", email="a@acc.test"):
    h, _ = register(client, org, email)
    return h


JAN = [("J1", "2026-01-05", 100, 60, "Widget"), ("J2", "2026-01-20", 200, 130, "Gadget")]
FEB = [("F1", "2026-02-05", 50, 20, "Widget"), ("F2", "2026-02-20", 150, 90, "Gadget")]


def test_a_second_monthly_file_adds_to_the_first_it_does_not_replace_it(client):
    h = setup(client)
    _, r1 = add_file(client, h, "sales_jan", JAN)
    assert total(client, h) == 300 and r1["files_in_model"] == 1 and r1["rows_replaced"] == 0
    _, r2 = add_file(client, h, "sales_feb", FEB)
    assert total(client, h) == 500                                        # 300 + 200: January is still there
    assert r2["rows_loaded"] == 2 and r2["fact_rows_total"] == 4 and r2["files_in_model"] == 2
    assert r2["rows_replaced"] == 0 and r2["orders_replaced"] == 0
    by = client.post("/api/v1/analytics/query", json={"question": "revenue by month"}, headers=h).json()
    assert {r[0]: float(r[1]) for r in by["rows"]} == {"2026-01": 300.0, "2026-02": 200.0}


def test_a_corrected_re_export_replaces_only_the_orders_it_contains_and_says_so(client):
    h = setup(client)
    add_file(client, h, "sales_jan", JAN)
    ds2, r2 = add_file(client, h, "sales_jan_corrected", [("J2", "2026-01-20", 250, 130, "Gadget"), ("J3", "2026-01-25", 10, 5, "Widget")])
    assert total(client, h) == 100 + 250 + 10                              # J1 kept, J2 corrected, J3 new
    assert r2["orders_replaced"] == 1 and r2["rows_replaced"] == 1 and r2["fact_rows_total"] == 3
    q = {x["rule"]: x for x in client.get(f"/api/v1/datasets/{ds2}/quality", headers=h).json()["results"]}
    assert q["replaced_by_later_file"]["status"] == "warn" and "1 orders" in q["replaced_by_later_file"]["message"]
    assert q["reconciliation"]["status"] == "pass"


def test_uploading_the_same_file_twice_does_not_double_count(client):
    h = setup(client)
    add_file(client, h, "sales_jan", JAN)
    _, r = add_file(client, h, "sales_jan_again", JAN)
    assert total(client, h) == 300 and r["rows_replaced"] == 2 and r["fact_rows_total"] == 2


def test_approving_the_same_dataset_again_rebuilds_it_without_duplicates(client):
    h = setup(client)
    ds, _ = add_file(client, h, "sales_jan", JAN)
    prof = client.get(f"/api/v1/datasets/{ds}/profile", headers=h).json()
    again = client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": prof["column_mapping"]}, headers=h).json()
    assert again["fact_rows_total"] == 2 and again["rows_replaced"] == 0 and total(client, h) == 300


def test_replace_existing_discards_every_other_files_rows_on_request(client):
    h = setup(client)
    add_file(client, h, "sales_jan", JAN)
    _, r = add_file(client, h, "sales_feb", FEB, replace_existing=True)
    assert total(client, h) == 200 and r["fact_rows_total"] == 2 and r["files_in_model"] == 1 and r["replace_all"] is True


def test_lines_of_the_same_order_inside_one_file_are_all_kept(client):
    h = setup(client)
    lines = [("O1", "2026-03-01", 40, 20, "Widget"), ("O1", "2026-03-01", 60, 30, "Gadget")]
    _, r = add_file(client, h, "multi_line", lines)
    assert total(client, h) == 100 and r["rows_loaded"] == 2 and r["rows_replaced"] == 0


def test_bad_rows_are_quarantined_per_file_and_earlier_files_quarantine_is_kept(client):
    h = setup(client)
    _, r1 = add_file(client, h, "sales_jan", JAN + [("B1", "not-a-date", 5, 1, "Widget")])
    assert r1["rows_loaded"] == 2 and r1["rows_rejected"] == 1
    _, r2 = add_file(client, h, "sales_feb", FEB + [("B2", "2026-02-01", "abc", 1, "Widget")])
    assert r2["rows_loaded"] == 2 and r2["rows_rejected"] == 1                      # reconciles for THIS file
    from sqlalchemy import select

    from app import models as m
    from app.services.backends import get_backend
    with SessionLocal() as db:
        t = db.scalar(select(m.Tenant.id).where(m.Tenant.name == "Acc"))
    assert get_backend().scalar(t, "SELECT count(*) FROM quarantine_sales") == 2   # both files' bad rows retained


def test_data_built_before_files_accumulated_is_kept_when_a_new_file_arrives(client):
    from sqlalchemy import select

    from app import models as m
    from app.services import analytics_store as duck
    h = setup(client)
    with SessionLocal() as db:
        t = db.scalar(select(m.Tenant.id).where(m.Tenant.name == "Acc"))
    con = duck.writer(t)           # the old layout: fact_sales and quarantine_sales only, no silver_sales
    con.execute("CREATE TABLE fact_sales AS SELECT 'L1' AS order_id, DATE '2025-12-01' AS order_date, "
                "CAST(70 AS DECIMAL(18,2)) AS net_sales, CAST(NULL AS DECIMAL(18,2)) AS cost_of_goods_sold, "
                "'Widget' AS product, CAST(NULL AS VARCHAR) AS region, CAST(NULL AS VARCHAR) AS customer")
    con.execute("CREATE TABLE quarantine_sales AS SELECT * FROM fact_sales WHERE false")
    con.close()
    _, r = add_file(client, h, "sales_jan", JAN)
    assert r["fact_rows_total"] == 3 and total(client, h) == 370


def test_accumulation_is_per_organisation(client):
    h1 = setup(client, "One", "1@acc.test")
    h2 = setup(client, "Two", "2@acc.test")
    add_file(client, h1, "sales_jan", JAN)
    add_file(client, h2, "sales_feb", FEB)
    assert total(client, h1) == 300 and total(client, h2) == 200


def test_automatic_modelling_accumulates_files_too(client):
    h = setup(client, "Auto", "a@acc2.test")
    assert client.patch("/api/v1/settings", json={"auto_model": True}, headers=h).status_code == 200
    add_file(client, h, "sales_jan", JAN, approve=False)
    add_file(client, h, "sales_feb", FEB, approve=False)
    assert total(client, h) == 500
    assert client.get("/api/v1/dashboard", headers=h).status_code == 200
