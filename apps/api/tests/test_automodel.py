"""Automatic modelling after upload: unambiguous files are modelled and the built-in metrics approved with no
clicks; ambiguous files wait for a person; every automatic decision is in the activity log; admins can turn it off."""
from .conftest import ingest_and_model, register
from .test_dimensions import ask, upload


def _auto(client, h, on=True):
    r = client.patch("/api/v1/settings", json={"auto_model": on}, headers=h)
    assert r.status_code == 200 and r.json()["auto_model"] is on


def _upload_and_run(client, h, name, data):
    ds, _ = ingest_and_model(client, h, name, data=data)
    return client.get(f"/api/v1/datasets/{ds}/profile", headers=h).json()


def test_clear_file_is_modelled_and_gross_profit_approved_without_any_click(client):
    h, _ = register(client, "Auto", "a@auto.test")
    _auto(client, h)
    prof = _upload_and_run(client, h, "orders_reference.csv", None)
    assert prof["model_status"] == "approved"
    assert prof["profile"]["auto"]["status"] == "built"
    assert "gross_profit" in prof["profile"]["auto"]["metrics_approved"]
    assert float(ask(client, h, "What is our gross profit?")["rows"][0][0]) == 140
    assert float(ask(client, h, "total revenue")["rows"][0][0]) == 350
    actions = [e["action"] for e in client.get("/api/v1/activity", headers=h).json()]
    assert "dataset.auto_approved" in actions and "metric.auto_approved" in actions


def test_unusual_column_names_are_still_recognised(client):
    h, _ = register(client, "Odd", "o@odd.test")
    _auto(client, h)
    data = (b"invoice_no,sale_day,total_turnover,purchase_cost,item\n"
            b"A,2026-01-10,100,60,Widget\nB,2026-01-20,200,130,Gadget\n")
    prof = _upload_and_run(client, h, "odd.csv", data)
    assert prof["profile"]["auto"]["status"] == "built", prof["profile"]["auto"]
    assert float(ask(client, h, "What is our gross profit?")["rows"][0][0]) == 110


def test_ambiguous_amount_column_waits_for_a_person(client):
    h, _ = register(client, "Amb", "a@amb.test")
    _auto(client, h)
    data = b"order_id,order_date,amount\nA,2026-01-01,10\nB,2026-01-02,20\n"
    prof = _upload_and_run(client, h, "amb.csv", data)
    assert prof["model_status"] == "proposed"
    assert prof["profile"]["auto"]["status"] == "needs_review"
    assert ask(client, h, "total revenue")["outcome"] != "answered"


def test_file_without_cost_models_sales_but_leaves_gross_profit_unapproved(client):
    h, _ = register(client, "NoCost", "n@nocost2.test")
    _auto(client, h)
    prof = _upload_and_run(client, h, "s.csv", b"order_id,order_date,net_sales\nA,2026-01-01,10\n")
    assert prof["model_status"] == "approved"
    assert "gross_profit" not in prof["profile"]["auto"]["metrics_approved"]
    assert float(ask(client, h, "total revenue")["rows"][0][0]) == 10
    assert ask(client, h, "gross profit")["outcome"] == "clarification"


def test_text_cost_column_is_ignored_not_trusted(client):
    h, _ = register(client, "Txt", "t@txt.test")
    _auto(client, h)
    data = b"order_id,order_date,net_sales,cost\nA,2026-01-01,10,n/a\nB,2026-01-02,20,tbc\n"
    prof = _upload_and_run(client, h, "t.csv", data)
    assert prof["model_status"] == "approved"
    assert "cost_of_goods_sold" not in prof["profile"]["auto"]["mapping"]
    assert "gross_profit" not in prof["profile"]["auto"]["metrics_approved"]


def test_switch_off_keeps_manual_approval(client):
    h, _ = register(client, "Manual", "m@manual.test")
    _auto(client, h, on=False)
    prof = _upload_and_run(client, h, "orders_reference.csv", None)
    assert prof["model_status"] == "proposed" and "auto" not in prof["profile"]


def test_only_admins_change_the_switch(client):
    h, _ = register(client, "Own", "o@own.test")
    r = client.post("/api/v1/users", headers=h,
                    json={"email": "v@own.test", "role": "viewer", "password": "viewer-pass-12"})
    assert r.status_code == 201, r.text
    login = client.post("/api/v1/auth/login", json={"email": "v@own.test", "password": "viewer-pass-12"})
    assert login.status_code == 200, login.text
    vh = {"Authorization": f"Bearer {login.json()['token']}"}
    assert client.patch("/api/v1/settings", json={"auto_model": True}, headers=vh).status_code == 403


def test_products_list_is_modelled_as_a_dimension_automatically(client):
    h, _ = register(client, "Dim", "d@dim.test")
    _auto(client, h)
    ds, _ = ingest_and_model(client, h, "products.csv")
    prof = client.get(f"/api/v1/datasets/{ds}/profile", headers=h).json()
    assert prof["model_status"] == "approved" and prof["profile"]["role"] == "dim_product"


def test_automatic_modelling_is_tenant_scoped(client):
    h1, _ = register(client, "One", "1@one.test")
    h2, _ = register(client, "Two", "2@two.test")
    _auto(client, h1)
    _upload_and_run(client, h1, "orders_reference.csv", None)
    assert ask(client, h2, "total revenue")["outcome"] != "answered"
