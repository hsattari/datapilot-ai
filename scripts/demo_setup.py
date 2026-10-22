"""Create demo data in your LOCAL DataPilot: register (or sign in), upload the reference CSV, approve the model and
Gross Profit. Needs the API and the worker running (see README). Safe to run again.

    python scripts\\demo_setup.py you@example.com          (asks for a password, 10+ characters)
"""
import getpass, json, pathlib, sys, time, urllib.error, urllib.request, uuid

API = "http://localhost:8000/api/v1"
ROOT = pathlib.Path(__file__).resolve().parents[1]


def call(path, body=None, token=None, raw=None, ctype="application/json"):
    req = urllib.request.Request(API + path, data=raw if raw is not None else (json.dumps(body).encode() if body is not None else None),
                                 method="POST" if (body is not None or raw is not None) else "GET",
                                 headers={"Content-Type": ctype, **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"API returned {e.code} for {path}: {e.read().decode(errors='replace')[:300]}\n"
                         + ("The API crashed: read the traceback in its window (database not running / migrations not applied?)" if e.code == 500 else ""))
    except urllib.error.URLError as e:
        raise SystemExit(f"Cannot reach the API at {API}: {e.reason}. Start it first.")


if len(sys.argv) < 2:
    sys.exit(__doc__)
email, pw = sys.argv[1], getpass.getpass("Password (10+ characters): ")
try:
    tok = call("/tenants", {"organisation": "Demo Ltd", "email": email, "password": pw})["token"]
    print("Registered a new demo workspace.")
except SystemExit:
    tok = call("/auth/login", {"email": email, "password": pw})["token"]
    print("Signed in to the existing workspace.")

sid = call("/data-sources", {"name": "orders_reference"}, tok)["id"]
bd = uuid.uuid4().hex
data = (ROOT / "data/synthetic/orders_reference.csv").read_bytes()
body = (f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="orders_reference.csv"\r\n'
        f'Content-Type: text/csv\r\n\r\n').encode() + data + f"\r\n--{bd}--\r\n".encode()
job = call(f"/data-sources/{sid}/uploads", token=tok, raw=body, ctype=f"multipart/form-data; boundary={bd}")["job_id"]
for _ in range(60):
    st = call(f"/jobs/{job}", token=tok)
    if st["status"] == "succeeded":
        break
    if st["status"] == "failed":
        sys.exit(f"Ingestion failed: {st['error_message']}")
    time.sleep(1)
else:
    sys.exit("Ingestion did not finish in 60 s. Is the worker running (python -m app.worker in apps\\api)?")
prof_all = [call(f"/datasets/{d['id']}/profile", token=tok) for d in call("/datasets", token=tok) if d["model_status"] != "approved"]
prof = prof_all[-1]
mapping = {k: v["column"] for k, v in prof["profile"]["suggested_mapping"].items() if not v.get("needs_clarification")}
print("Model:", call(f"/datasets/{prof['id']}/approve", {"mapping": mapping}, tok))
print("Gross profit:", call("/metrics/gross_profit/approve", {}, tok))
print("Done. Now run: python scripts\\export_powerbi.py", email)
