"""End-to-end smoke test of a DEPLOYED environment, standard library only.

    python scripts/smoke_test.py https://<web address>

Signs up a throwaway organisation, uploads data/synthetic/orders_reference.csv, waits for the worker, checks the two
reference numbers (net revenue 350, gross profit 140), checks the Overview dashboard exists, then deletes the
organisation. Exit code 1 on any failure. Allow a few minutes after a deploy: the malware scanner needs time to
download its signatures and uploads are refused (503) until it is ready, so the upload is retried.
"""
import json
import pathlib
import secrets
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else ""
CSV = pathlib.Path(__file__).resolve().parents[1] / "data" / "synthetic" / "orders_reference.csv"


def call(method, path, body=None, token=None, raw=None, ctype=None, retries=0):
    for attempt in range(retries + 1):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(BASE + "/api/v1" + path, data=data, method=method)
        req.add_header("Content-Type", ctype or "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode()
                return r.status, (json.loads(text) if text else None)
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < retries:
                time.sleep(15)
                continue
            return e.code, e.read().decode()[:300]
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(15)
                continue
            raise
    return 0, None


def check(label, ok, detail=""):
    print(("OK   " if ok else "FAIL ") + label + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        sys.exit(1)


if not BASE.startswith("http"):
    sys.exit("usage: smoke_test.py https://<web address>")
org, email, pw = "Smoke " + secrets.token_hex(3), f"smoke-{secrets.token_hex(4)}@example.com", secrets.token_urlsafe(16)
st, r = call("POST", "/tenants", {"organisation": org, "email": email, "password": pw, "accept_terms": True}, retries=8)
check("sign up", st == 201, f"{st} {r}")
tok = r["token"]
st, r = call("POST", "/data-sources", {"name": "smoke.csv"}, tok)
check("create a source", st == 201, f"{st} {r}")
sid, boundary = r["id"], "----smoke" + secrets.token_hex(6)
head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="orders_reference.csv"\r\n'
        f"Content-Type: text/csv\r\n\r\n").encode()
body = head + CSV.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
st, r = call("POST", f"/data-sources/{sid}/uploads", token=tok, raw=body, ctype=f"multipart/form-data; boundary={boundary}", retries=20)
check("upload accepted (scanner ready)", st == 202, f"{st} {r}")
job = r["job_id"]
j = {}
for _ in range(60):
    st, j = call("GET", f"/jobs/{job}", token=tok)
    if st == 200 and j["status"] in ("succeeded", "failed"):
        break
    time.sleep(5)
check("worker processed the file", st == 200 and j["status"] == "succeeded", str(j))
res = {}
for _ in range(30):                                     # automatic modelling follows the upload
    st, res = call("POST", "/analytics/query", {"question": "What is our gross profit?"}, tok)
    if st == 200 and isinstance(res, dict) and res.get("rows"):
        break
    time.sleep(3)
check("gross profit is 140", st == 200 and float(res["rows"][0][0]) == 140, str(res)[:200])
st, res = call("POST", "/analytics/query", {"question": "total revenue"}, tok)
check("net revenue is 350", st == 200 and float(res["rows"][0][0]) == 350, str(res)[:200])
st, d = call("GET", "/dashboards", token=tok)
check("an Overview dashboard exists", st == 200 and any(x["title"] == "Overview" for x in d), str(d)[:200])
st, r = call("POST", "/tenant/delete", {"confirm_name": org, "password": pw}, tok)
check("throwaway organisation deleted", st in (200, 202, 204), f"{st} {r}")
print("SMOKE TEST PASSED")
