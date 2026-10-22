"""Browser end-to-end test of the web UI (Playwright + Chromium). Needs API, worker and web running:

    # terminal 1: cd apps/api && alembic upgrade head && uvicorn app.main:app
    # terminal 2: cd apps/api && python -m app.worker
    # terminal 3: cd apps/web && npm run dev
    pip install playwright && playwright install chromium
    python scripts/e2e_ui_test.py [screenshot_dir]      (set CHROMIUM_PATH to use a specific browser binary)

Registers a user, uploads the reference CSV, approves the model, checks the dashboard (350 / 140), asks for gross
profit (140) and asks revenue by category after approving a products file, checks an unsafe question is refused, then saves/runs a report, triggers an alert, edits settings and checks the activity log. Gross profit is approved through the Business Metrics screen; the quality panel is checked.
"""
import json, sys, urllib.request
from playwright.sync_api import sync_playwright, expect

import pathlib
REPO = str(pathlib.Path(__file__).resolve().parents[1])
S = sys.argv[1] if len(sys.argv) > 1 else "."  # screenshot dir
import time
EMAIL, PW = f"ui{int(time.time())}@test.example", "correct-horse-1"

def api(method, path, body=None, token=None):
    req = urllib.request.Request("http://localhost:8000" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    return json.load(urllib.request.urlopen(req))

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
    expect(page.get_by_text("Ingested.")).to_be_visible(timeout=30000)
    print("OK upload -> worker ingested")
    page.get_by_text("orders_reference").first.click()
    expect(page.get_by_text("Proposed mapping")).to_be_visible()
    page.screenshot(path=f"{S}/profile.png")
    page.get_by_role("button", name="Approve and build model").click()
    expect(page.get_by_text("Model built: 3 rows loaded, 0 quarantined.")).to_be_visible(timeout=15000)
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
    page.locator("input[type=file]").set_input_files(f"{REPO}/data/synthetic/products.csv")
    expect(page.get_by_text("Ingested.")).to_be_visible(timeout=30000)
    page.get_by_role("link", name="products", exact=True).click()
    expect(page.get_by_text("This looks like a products list")).to_be_visible()
    page.get_by_role("button", name="Approve as products list").click()
    expect(page.get_by_text("Products list built: 2 loaded, 0 quarantined.")).to_be_visible(timeout=15000)
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
    b.close()
print("JS errors:", errors or "none")
