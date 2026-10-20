"""Browser end-to-end test of the web UI (Playwright + Chromium). Needs API, worker and web running:

    # terminal 1: cd apps/api && alembic upgrade head && uvicorn app.main:app
    # terminal 2: cd apps/api && python -m app.worker
    # terminal 3: cd apps/web && npm run dev
    pip install playwright && playwright install chromium
    python scripts/e2e_ui_test.py [screenshot_dir]      (set CHROMIUM_PATH to use a specific browser binary)

Registers a user, uploads the reference CSV, approves the model, checks the dashboard (350 / 140), asks for gross
profit (140) and checks an unsafe question is refused. Metric approval has no UI yet, so it is done via the API.
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

    tok = api("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PW})["token"]
    api("POST", "/api/v1/metrics/gross_profit/approve", token=tok)  # no UI for this yet (known gap)

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

    page.get_by_placeholder("e.g. Why has gross profit").fill("drop table fact_sales")
    page.get_by_role("button", name="Ask", exact=True).click()
    expect(page.get_by_text("read-only questions")).to_be_visible(timeout=10000)
    print("OK unsafe question refused in UI")
    b.close()
print("JS errors:", errors or "none")
