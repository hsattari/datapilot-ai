"""XML upload: records become rows, nested data is flattened without loss, and XML attacks are refused."""
import pytest

from app.services.profiling import parse_file, parse_xml
from app.services.validation import UploadRejected, validate_upload

from .conftest import ingest_and_model, register
from .test_dimensions import ask

ORDERS = b"""<?xml version="1.0" encoding="UTF-8"?>
<orders>
  <order id="A"><date>2026-01-10</date><net_sales>100</net_sales><cost>60</cost>
    <customer><name>Ann</name><city>Leeds</city></customer></order>
  <order id="B"><date>2026-01-20</date><net_sales>200</net_sales><cost>130</cost>
    <customer><name>Bob</name><city>York</city></customer></order>
  <order id="C"><date>2026-02-05</date><net_sales>50</net_sales><cost>20</cost>
    <customer><name>Cy</name><city>Hull</city></customer></order>
</orders>"""


def test_repeating_elements_become_rows_and_nested_fields_become_columns():
    df = parse_xml(ORDERS)
    assert len(df) == 3
    assert list(df.columns) == ["id", "date", "net_sales", "cost", "customer_name", "customer_city"]
    assert df["net_sales"].tolist() == ["100", "200", "50"] and df["customer_city"].tolist() == ["Leeds", "York", "Hull"]


def test_records_nested_one_level_down_are_found():
    xml = b"<feed><meta>x</meta><body><row><a>1</a></row><row><a>2</a></row></body></feed>"
    df = parse_xml(xml)
    assert df["a"].tolist() == ["1", "2"]


def test_repeated_children_inside_a_record_are_kept_not_dropped():
    xml = b"<o><order><id>1</id><line><sku>A</sku><qty>2</qty></line><line><sku>B</sku><qty>1</qty></line></order><order><id>2</id></order></o>"
    df = parse_xml(xml)
    assert df.loc[0, "line"] == '[{"sku": "A", "qty": "2"}, {"sku": "B", "qty": "1"}]' and df.loc[1, "line"] == ""


def test_namespaces_attributes_and_name_clashes():
    xml = b'<r xmlns="urn:x"><item id="1"><id>child</id></item><item id="2"><id>c2</id></item></r>'
    df = parse_xml(xml)
    assert len(df) == 2 and set(df.columns) == {"id", "id_el"} and df["id"].tolist() == ["1", "2"]


def test_single_record_file_is_one_row():
    assert parse_xml(b"<order><id>1</id><total>5</total></order>").to_dict("records") == [{"id": "1", "total": "5"}]


@pytest.mark.parametrize("name,payload", [
    ("xxe", b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r><a>&x;</a><a>1</a></r>'),
    ("bomb", b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><l>&b;</l>'),
    ("remote_dtd", b'<!DOCTYPE r SYSTEM "http://evil.example/x.dtd"><r><a>1</a><a>2</a></r>'),
    ("lowercase", b'<!doctype r [<!entity x "y">]><r><a>&x;</a></r>'),
])
def test_xml_attacks_are_refused_at_upload_and_again_by_the_parser(name, payload):
    with pytest.raises(UploadRejected):
        validate_upload(f"{name}.xml", payload, 25)
    with pytest.raises(ValueError):                      # defence in depth: the parser itself refuses them too
        parse_xml(payload)


def test_parser_refuses_entities_even_if_validation_is_bypassed():
    with pytest.raises(ValueError, match="not accepted"):
        parse_xml(b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "y">]><r><a>&x;</a></r>')


def test_malformed_and_deep_and_non_xml_files_are_refused():
    with pytest.raises(ValueError, match="not well formed"):
        parse_xml(b"<a><b></a>")
    with pytest.raises(ValueError, match="too deeply nested"):
        parse_xml(b"<a>" * 100 + b"x" + b"</a>" * 100)
    with pytest.raises(UploadRejected):
        validate_upload("x.xml", b"this is not xml", 25)
    with pytest.raises(UploadRejected):
        validate_upload("x.xml", b"", 25)


def test_wide_xml_is_refused():
    row = b"<r>" + b"".join(b"<c%d>1</c%d>" % (i, i) for i in range(600)) + b"</r>"
    with pytest.raises(ValueError, match="columns"):
        parse_xml(b"<t>" + row + row + b"</t>")


def test_parse_file_dispatches_xml_by_extension():
    frames = parse_file("orders.xml", ORDERS, 1000)
    assert list(frames) == ["orders"] and len(frames["orders"]) == 3


def test_xml_file_flows_through_to_a_governed_answer(client):
    h, _ = register(client, "Xml", "x@xml.test")
    ds, mapping = ingest_and_model(client, h, "orders.xml", data=ORDERS)
    assert mapping["net_sales"] == "net_sales" and mapping["cost_of_goods_sold"] == "cost"
    # the order id is the 'id' attribute and the date is 'date': neither name is in the fixed synonym list as written
    assert "order_id" in mapping and "order_date" in mapping
    assert client.post(f"/api/v1/datasets/{ds}/approve", json={"mapping": mapping}, headers=h).status_code == 200
    client.post("/api/v1/metrics/gross_profit/approve", headers=h)
    assert float(ask(client, h, "What is our gross profit?")["rows"][0][0]) == 140


def test_xml_upload_endpoint_rejects_an_attack_without_storing_it(client):
    h, _ = register(client, "XmlBad", "b@xml.test")
    sid = client.post("/api/v1/data-sources", json={"name": "x"}, headers=h).json()["id"]
    bad = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r><a>&x;</a></r>'
    r = client.post(f"/api/v1/data-sources/{sid}/uploads", headers=h, files={"file": ("evil.xml", bad, "text/xml")})
    assert r.status_code == 422 and "DOCTYPE" in r.json()["detail"]
    assert client.get("/api/v1/jobs", headers=h).json() == []


def test_a_bare_id_column_is_not_an_order_key_unless_the_file_is_clearly_sales(client):
    from app.services.profiling import profile_frame, suggest_mapping
    import pandas as pd
    products = profile_frame(pd.DataFrame({"id": ["1", "2"], "name": ["a", "b"], "category": ["x", "y"]}))
    assert "order_id" not in suggest_mapping(products)
    sales = profile_frame(pd.DataFrame({"id": ["1", "2"], "date": ["2026-01-01", "2026-01-02"], "net_sales": ["5", "6"]}))
    assert suggest_mapping(sales)["order_id"]["column"] == "id"
    two_keys = profile_frame(pd.DataFrame({"id": ["1"], "ref": ["r"], "date": ["2026-01-01"], "net_sales": ["5"]}))
    assert "order_id" not in suggest_mapping(two_keys)               # two candidates: ask rather than guess
