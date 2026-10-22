"""One command to prepare the reference demo and export the Power BI files.

    python scripts/demo_setup.py you@example.com "a-password-of-10-or-more-chars"

Needs the API (and the worker, which processes uploads) running on http://localhost:8000.
It registers the email (or signs in if it already exists with that password), uploads data/synthetic/orders_reference.csv,
approves the model and the Gross Profit metric, checks the numbers (Net Revenue 350, Gross Profit 140), and then
writes powerbi_export/ (measures.dax, model.bim, fact_sales.csv) inside the project folder.
"""
import json, os, pathlib, subprocess, sys, time, urllib.error, urllib.request, uuid

API = "http://localhost:8000/api/v1"
ROOT = pathlib.Path(__file__).resolve().parents[1]


def call(path, body=None, token=None, raw=None, ctype="application/json", ok_codes=()):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(API + path, data=data, method="POST" if (data is not None or ok_codes == "post") else "GET",
                                 headers={"Content-Type": ctype, **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        if e.code in ok_codes:
            return {"_status": e.code}
        sys.exit(f"STOP: {path} returned {e.code}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"STOP: cannot reach the API at {API} ({e.reason}). Start it first: cd apps\\api ; uvicorn app.main:app")


if len(sys.argv) != 3:
    sys.exit(__doc__)
email, pw = sys.argv[1], sys.argv[2]
r = call("/tenants", {"organisation": "Demo Ltd", "email": email, "password": pw}, ok_codes=(409,))
if r.get("_status") == 409:
    print("Email already registered: signing in with the password you gave.")
    r = call("/auth/login", {"email": email, "password": pw})
tok = r["token"]
print("1/6 signed in")

sid = call("/data-sources", {"name": "reference"}, tok)["id"]
bd = uuid.uuid4().hex
csv = (ROOT / "data/synthetic/orders_reference.csv").read_bytes()
body = (f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="orders_reference.csv"\r\n'
        f'Content-Type: text/csv\r\n\r\n').encode() + csv + f"\r\n--{bd}--\r\n".encode()
job = call(f"/data-sources/{sid}/uploads", token=tok, raw=body, ctype=f"multipart/form-data; boundary={bd}")["job_id"]
for _ in range(60):
    st = call(f"/jobs/{job}", token=tok)
    if st["status"] == "succeeded":
        break
    if st["status"] == "failed":
        sys.exit(f"STOP: ingestion failed: {st['error_message']}")
    time.sleep(1)
else:
    sys.exit("STOP: the upload is still queued after 60 s. The worker is probably not running: "
             "open a window in apps\\api and run  python -m app.worker  (with the same DATABASE_URL as the API).")
print("2/6 file uploaded and ingested")

# newest dataset named orders_reference
ds = [d for d in call("/datasets", token=tok) if d["name"] == "orders_reference"][-1]
prof = call(f"/datasets/{ds['id']}/profile", token=tok)["profile"]
mapping = {k: v["column"] for k, v in prof["suggested_mapping"].items() if not v.get("needs_clarification")}
res = call(f"/datasets/{ds['id']}/approve", {"mapping": mapping}, tok)
print(f"3/6 model approved: {res['rows_loaded']} rows loaded, {res['rows_rejected']} quarantined")
call("/metrics/gross_profit/approve", {}, tok)
print("4/6 Gross Profit approved")

tiles = {t["metric"]: t["total"] for t in call("/dashboard", token=tok)["tiles"]}
print(f"5/6 app says: Net Revenue {tiles.get('net_revenue')}, Orders {tiles.get('orders')}, Gross Profit {tiles.get('gross_profit')}")
if float(tiles.get("gross_profit", 0)) != 140 or float(tiles.get("net_revenue", 0)) != 350:
    print("WARNING: expected 350 and 140 for a fresh account. This account may already hold other data.")

env = {**os.environ, "DATAPILOT_PASSWORD": pw}
subprocess.run([sys.executable, str(ROOT / "scripts" / "export_powerbi.py"), email], env=env, check=True)
print("6/6 done. Next: open Power BI Desktop and follow docs/architecture/fabric-powerbi.md (Checking the measures).")
