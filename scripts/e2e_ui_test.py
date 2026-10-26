"""Browser end-to-end test of the web UI (Playwright + Chromium). Needs API, worker and web running:

    # terminal 1: cd apps/api && alembic upgrade head && uvicorn app.main:app
    # terminal 2: cd apps/api && python -m app.worker
    # terminal 3: cd apps/web && npm run dev
    pip install playwright && playwright install chromium
    python scripts/e2e_ui_test.py [screenshot_dir]      (set CHROMIUM_PATH to use a specific browser binary)

Registers a user, uploads the reference CSV, approves the model, checks the dashboard (350 / 140), asks for gross
profit (140) and asks revenue by category after approving a products file, checks an unsafe question is refused, then saves/runs a report, triggers an alert, edits settings, checks the activity log, adds and erases a viewer, turns on two-step sign-in, downloads the data export and finally deletes the organisation. Gross profit is approved through the Business Metrics screen; the quality panel is checked.
"""
import base64, hashlib, hmac, json, struct, sys, time as _t, urllib.error, urllib.request
from playwright.sync_api import sync_playwright, expect

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[1])
S = sys.argv[1] if len(sys.argv) > 1 else "."  # screenshot dir
import time
STAMP = int(time.time())
EMAIL, PW = f"ui{STAMP}@test.example", "correct-horse-1"

def api(method, path, body=None, token=None):
    req = urllib.request.Request("http://localhost:8000" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    return json.load(urllib.request.urlopen(req))


def totp(secret, offset=0):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", int(_t.time() // 30) + offset), hashlib.sha1).digest()
    o = mac[-1] & 15
    return str((struct.unpack(">I", mac[o:o + 4])[0] & 0x7FFFFFFF) % 10**6).zfill(6)

errors = []
with sync_playwright() as p:
    import os
    kw = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}
    b = p.chromium.launch(args=["--no-sandbox"], **kw)
    page = b.new_page(viewport={"width": 1100, "height": 800})
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
    page.goto("http://localhost:3000")
    page.get_by_placeholder("Organisation").fill("UI Test Ltd")
    page.get_by_placeholder("Email").fill(EMAIL)
    page.get_by_placeholder("Password (10+ chars)").fill(PW)
    page.get_by_role("button", name="Create workspace").click()
    expect(page.get_by_role("button", name="Data Sources")).to_be_visible(timeout=10000)
    print("OK register -> logged in")

    page.get_by_role("button", name="Data Sources").click()
    page.locator("input[type=file]").set_input_files(f"{REPO}/data/synthetic/orders_reference.csv")
    expect(page.get_by_test_id("upload-results")).to_contain_text("orders_reference.csv - ingested", timeout=30000)
    print("OK upload -> worker ingested")
    page.get_by_role("link", name="orders_reference").first.click()
    expect(page.get_by_text("Proposed mapping")).to_be_visible()
    expect(page.get_by_label("Cost of goods sold")).to_have_value("cost")   # suggested, and changeable by the person
    expect(page.get_by_label("Net sales (revenue)")).to_have_value("net_sales")
    page.get_by_label("Customer").select_option("")   # the person drops a field; it must stay dropped after saving
    page.screenshot(path=f"{S}/profile.png")
    page.get_by_role("button", name="Approve and build model").click()
    expect(page.get_by_test_id("approve-note")).to_contain_text("Saved. Model built: 3 rows loaded, 0 quarantined.", timeout=15000)
    expect(page.get_by_test_id("model-state")).to_have_text("Approved")
    expect(page.get_by_label("Customer")).to_have_value("")                    # the saved choice, not the old suggestion
    expect(page.get_by_label("Cost of goods sold")).to_have_value("cost")
    page.get_by_role("link", name="orders_reference").first.click()             # reopen: the saved columns are shown
    expect(page.get_by_label("Customer")).to_have_value("")
    print("OK profile shown, mapping approved, model built")

    expect(page.get_by_test_id("quality")).to_contain_text("Data quality: warn", timeout=10000)  # reference data is old: freshness warns
    expect(page.get_by_test_id("quality")).to_contain_text("reconciliation")
    page.screenshot(path=f"{S}/quality.png")
    expect(page.get_by_test_id("quality")).to_contain_text("older than 90 days")
    print("OK data-quality panel shows reconciliation pass and a stale-data warning")

    page.get_by_role("button", name="Business Metrics").click()
    row = page.locator("tr[data-metric=gross_profit]")
    expect(row).to_contain_text("Needs approval", timeout=10000)
    row.get_by_role("button", name="Approve Gross Profit").click()
    expect(row).to_contain_text("Approved", timeout=10000)
    page.screenshot(path=f"{S}/metrics.png")
    print("OK gross profit approved in the UI")

    page.get_by_role("button", name="Home").click()
    expect(page.get_by_text("Net Revenue: 350")).to_be_visible(timeout=10000)
    expect(page.get_by_text("Gross Profit: 140")).to_be_visible(timeout=10000)
    page.screenshot(path=f"{S}/home.png")
    print("OK dashboard shows Net Revenue 350, Gross Profit 140")

    page.get_by_role("button", name="Ask Your Data").click()
    page.get_by_placeholder("e.g. Why has gross profit").fill("What is our gross profit?")
    page.get_by_role("button", name="Ask", exact=True).click()
    expect(page.get_by_text("How this was calculated")).to_be_visible(timeout=10000)
    assert page.locator("table td").first.inner_text().startswith("140"), page.locator("table").inner_text()
    page.get_by_text("How this was calculated").click()
    expect(page.locator("code", has_text="net_sales - cost_of_goods_sold")).to_be_visible()
    page.screenshot(path=f"{S}/ask.png")
    print("OK ask -> 140 with definition shown")

    page.get_by_role("button", name="Data Sources").click()
    # Several files at once: an unsupported file first must fail on its own without stopping the real one.
    import tempfile
    bad = pathlib.Path(tempfile.mkdtemp()) / "notes.exe"
    bad.write_bytes(b"MZ not a data file")
    page.locator("input[type=file]").set_input_files([str(bad), f"{REPO}/data/synthetic/products.csv"])
    expect(page.get_by_test_id("upload-results")).to_contain_text("products.csv - ingested", timeout=30000)
    expect(page.get_by_test_id("upload-results")).to_contain_text("notes.exe - failed")
    expect(page.get_by_text("2 files processed.")).to_be_visible()
    print("OK two files at once: the unsupported one failed alone, the other was ingested")
    page.get_by_role("link", name="products", exact=True).click()
    expect(page.get_by_text("This looks like a products list")).to_be_visible()
    page.get_by_role("button", name="Approve as products list").click()
    expect(page.get_by_test_id("approve-note")).to_contain_text("Products list built: 2 loaded, 0 quarantined.", timeout=15000)
    expect(page.get_by_test_id("joins")).to_contain_text("2 of 2 (100%)", timeout=10000)
    page.screenshot(path=f"{S}/joins.png")
    print("OK products file recognised, approved as a dimension, joins to sales at 100%")

    page.get_by_role("button", name="Ask Your Data").click()
    page.get_by_placeholder("e.g. Why has gross profit").fill("net revenue by category")
    page.get_by_role("button", name="Ask", exact=True).click()
    expect(page.get_by_text("How this was calculated")).to_be_visible(timeout=10000)
    cells = page.locator("table td").all_inner_texts()
    assert cells[:4] == ["Accessories", "200", "Hardware", "150"], cells
    print("OK ask by category -> Accessories 200, Hardware 150")

    page.get_by_placeholder("e.g. Why has gross profit").fill("drop table fact_sales")
    page.get_by_role("button", name="Ask", exact=True).click()
    expect(page.get_by_text("read-only questions")).to_be_visible(timeout=10000)
    print("OK unsafe question refused in UI")

    page.get_by_placeholder("e.g. Why has gross profit").fill("What is our gross profit?")
    page.get_by_role("button", name="Ask", exact=True).click()
    expect(page.get_by_text("How this was calculated")).to_be_visible(timeout=10000)
    page.get_by_role("button", name="Save as report").click()
    expect(page.get_by_text("Saved under Reports.")).to_be_visible(timeout=10000)
    page.get_by_role("button", name="Reports").click()
    expect(page.get_by_text("What is our gross profit?").first).to_be_visible(timeout=10000)
    page.get_by_role("button", name="Run").click()
    expect(page.get_by_test_id("report-result")).to_contain_text("140", timeout=10000)
    print("OK question saved as a report and re-run (140)")

    page.get_by_role("button", name="Alerts").click()
    page.get_by_placeholder("Name, e.g. Profit below target").fill("Profit above 100")
    page.locator("select").nth(0).select_option(label="Gross Profit")
    page.locator("select").nth(1).select_option(label="is above")
    page.get_by_placeholder("Threshold").fill("100")
    page.get_by_role("button", name="Create alert").click()
    expect(page.locator("tr[data-alert='Profit above 100']")).to_be_visible(timeout=10000)
    page.get_by_role("button", name="Check now").click()
    expect(page.locator("tr[data-alert='Profit above 100']")).to_contain_text("triggered", timeout=10000)
    expect(page.locator("tr[data-alert='Profit above 100']")).to_contain_text("gross_profit above 100")  # the metric I picked
    expect(page.locator("tr[data-alert='Profit above 100']")).to_contain_text("140")
    page.screenshot(path=f"{S}/alerts.png")
    print("OK alert created and triggered (gross profit 140 > 100)")

    page.get_by_role("button", name="Settings").click()
    expect(page.get_by_text("Your role:")).to_contain_text("admin", timeout=10000)
    page.get_by_label("Currency").fill("EUR")
    page.get_by_role("button", name="Save settings").click()
    expect(page.get_by_text("Saved.")).to_be_visible(timeout=10000)
    tok = api("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PW})["token"]
    stored = api("GET", "/api/v1/settings", token=tok)
    assert stored["currency"] == "EUR", f"settings were not stored: {stored}"  # check the server, not the screen
    page.get_by_role("button", name="Activity").click()
    expect(page.get_by_test_id("usage")).to_contain_text("2 files", timeout=10000)
    expect(page.get_by_text("alert.triggered").first).to_be_visible(timeout=10000)
    expect(page.get_by_text("settings.updated").first).to_be_visible()
    page.screenshot(path=f"{S}/activity.png")
    print("OK settings saved; activity log, jobs and usage visible")

    page.get_by_role("button", name="Settings").click()
    expect(page.get_by_test_id("users")).to_be_visible(timeout=10000)
    page.get_by_placeholder("Email", exact=True).fill(f"viewer.{STAMP}@test.example")
    page.get_by_placeholder("Initial password (10+ characters)").fill("viewer-pass-123")
    page.get_by_role("button", name="Add person").click()
    expect(page.locator(f"tr[data-user='viewer.{STAMP}@test.example']")).to_contain_text("active", timeout=10000)
    vt = api("POST", "/api/v1/auth/login", {"email": f"viewer.{STAMP}@test.example", "password": "viewer-pass-123"})["token"]
    try:
        api("PATCH", "/api/v1/settings", {"name": "Hacked"}, token=vt); raise SystemExit("viewer must not change settings")
    except urllib.error.HTTPError as e:
        assert e.code == 403, e.code
    print("OK person added in the UI; the viewer cannot change settings")

    row = page.locator(f"tr[data-user='viewer.{STAMP}@test.example']")
    row.get_by_role("button", name="Erase", exact=True).click()
    row.get_by_role("button", name="Confirm erase").click()
    expect(page.locator(f"tr[data-user='viewer.{STAMP}@test.example']")).to_have_count(0, timeout=10000)
    try:
        api("POST", "/api/v1/auth/login", {"email": f"viewer.{STAMP}@test.example", "password": "viewer-pass-123"}); raise SystemExit("erased person must not sign in")
    except urllib.error.HTTPError as e:
        assert e.code == 401
    print("OK person erased in the UI; they can no longer sign in")

    page.get_by_role("button", name="Set up two-step sign-in").click()
    secret = page.get_by_test_id("mfa-secret").inner_text()
    page.get_by_placeholder("6-digit code").fill(totp(secret))
    page.get_by_role("button", name="Turn on").click()
    expect(page.get_by_test_id("recovery-codes")).to_be_visible(timeout=10000)
    expect(page.get_by_test_id("security")).to_contain_text("two-step sign-in is on")
    try:
        api("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PW}); raise SystemExit("login must need a code now")
    except urllib.error.HTTPError as e:
        assert e.code == 401 and b"mfa_required" in e.read()
    api("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PW, "code": totp(secret, 1)})  # the next 30-second code is accepted
    codes = [c.strip() for c in page.get_by_test_id("recovery-codes").inner_text().split("\n") if c.strip()]
    recovery, spare = codes[-1], codes[-2]
    assert len(recovery) == 14 and recovery.count("-") == 2 and len(spare) == 14, codes

    fresh = b.new_page()  # a different browser session: sign in through the screen
    fresh.goto("http://localhost:3000")
    fresh.get_by_role("link", name="Sign in").click()
    fresh.get_by_placeholder("Email").fill(EMAIL)
    fresh.get_by_placeholder("Password (10+ chars)").fill(PW)
    fresh.get_by_role("button", name="Sign in").click()
    expect(fresh.get_by_text("Enter the 6-digit code")).to_be_visible(timeout=10000)
    fresh.get_by_placeholder("Authenticator or recovery code").fill(recovery)
    fresh.get_by_role("button", name="Sign in").click()
    expect(fresh.get_by_role("button", name="Data Sources")).to_be_visible(timeout=10000)
    fresh.close()
    try:
        api("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PW, "code": recovery}); raise SystemExit("recovery code must be single-use")
    except urllib.error.HTTPError as e:
        assert e.code == 401
    print("OK sign-in screen asks for the code; a recovery code works once and then is refused")
    print("OK two-step sign-in turned on in the UI; the server now demands a code")

    page.get_by_label("Include original uploaded files").check()
    with page.expect_download() as dl:
        page.get_by_role("button", name="Download export").click()
    import zipfile
    z = zipfile.ZipFile(dl.value.path())
    n = z.namelist()
    assert "data/fact_sales.csv" in n and "data/dim_product.csv" in n and any(x.startswith("raw/") for x in n), n
    assert b"$2b$" not in b"".join(z.read(x) for x in n), "password hash leaked into the export"
    print("OK export downloaded from the UI: tables, products list and original files, no secrets")

    page.get_by_placeholder("Type the organisation name exactly").fill("UI Test Ltd")
    page.get_by_placeholder("Your password").fill(PW)
    page.get_by_role("button", name="Delete organisation and all data").click()
    expect(page.get_by_text("Invalid credentials")).to_be_visible(timeout=10000)  # two-step is on: password alone is refused
    assert errors and "401" in errors[-1], errors
    errors.pop()  # that 401 is the refusal we just provoked on purpose
    page.get_by_placeholder("Two-step code or recovery code").fill(spare)
    page.get_by_role("button", name="Delete organisation and all data").click()
    expect(page.get_by_role("button", name="Create workspace")).to_be_visible(timeout=15000)  # back at the sign-up screen
    try:
        api("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PW, "code": totp(secret, 1)}); raise SystemExit("deleted organisation must not sign in")
    except urllib.error.HTTPError as e:
        assert e.code == 401
    print("OK organisation deleted from the UI (needed password + two-step code); sign-in no longer works")
    b.close()
print("JS errors:", errors or "none")
