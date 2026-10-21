"""Sign in to your local DataPilot API and save the Power BI export as files you can use in Power BI Desktop.

    python scripts/export_powerbi.py you@example.com            (asks for the password; API on http://localhost:8000)

Writes to ./powerbi_export/:
    measures.dax   - the measures generated from your APPROVED metrics (one 'Name = expression' per measure)
    model.bim      - full TMSL model (for Tabular Editor / XMLA tools)
    fact_sales.csv - the reference data with the canonical column names the measures expect
                     (only written if data/synthetic/orders_reference.csv exists)
"""
import csv, getpass, json, pathlib, sys, urllib.request

API = "http://localhost:8000/api/v1"


def call(path, body=None, token=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    return json.load(urllib.request.urlopen(req))


if len(sys.argv) < 2:
    sys.exit(__doc__)
tok = call("/auth/login", {"email": sys.argv[1], "password": getpass.getpass("Password: ")})["token"]
out = call("/export/powerbi", token=tok)
d = pathlib.Path("powerbi_export"); d.mkdir(exist_ok=True)
(d / "measures.dax").write_text(out["measures_dax"], encoding="utf-8")
(d / "model.bim").write_text(json.dumps(out["model_bim"], indent=2), encoding="utf-8")
print("Measures exported:", [m["name"] for m in out["model_bim"]["model"]["tables"][0]["measures"]])
if out["skipped"]:
    print("Skipped (not approved or not convertible):", out["skipped"])
src = pathlib.Path(__file__).resolve().parents[1] / "data/synthetic/orders_reference.csv"
if src.exists():
    with open(src, newline="", encoding="utf-8") as f, open(d / "fact_sales.csv", "w", newline="", encoding="utf-8") as g:
        r = csv.DictReader(f)
        cols = ["order_id", "order_date", "product", "region", "customer", "net_sales", "cost_of_goods_sold"]
        w = csv.DictWriter(g, fieldnames=cols); w.writeheader()
        for row in r:
            row["cost_of_goods_sold"] = row.pop("cost"); w.writerow({c: row[c] for c in cols})
print(f"Files written to {d.resolve()}")
