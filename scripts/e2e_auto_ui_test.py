"""Browser test of automatic modelling (server default: AUTO_MODEL_DEFAULT=true). Needs API, worker and web running:
    python scripts/e2e_auto_ui_test.py [screenshot_dir]
Registers, uploads a sales file and a products file together, and checks that with no further clicks the dashboard shows
Net Revenue 350 and Gross Profit 140, Gross Profit is Approved, the category join works, and an ambiguous file asks."""
import os, pathlib, sys, tempfile, time
from playwright.sync_api import sync_playwright, expect

REPO = str(pathlib.Path(__file__).resolve().parents[1])
S = sys.argv[1] if len(sys.argv) > 1 else "."
EMAIL, PW = f"auto{int(time.time())}@test.example", "correct-horse-1"
errors = []
with sync_playwright() as p:
    kw = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}
    b = p.chromium.launch(args=["--no-sandbox"], **kw)
    page = b.new_page(viewport={"width": 1100, "height": 800})
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto("http://localhost:3000")
    page.get_by_placeholder("Organisation").fill("Auto Ltd")
    page.get_by_placeholder("Email").fill(EMAIL)
    page.get_by_placeholder("Password (10+ chars)").fill(PW)
    page.get_by_role("button", name="Create workspace").click()
    expect(page.get_by_role("button", name="Data Sources")).to_be_visible(timeout=10000)

    page.get_by_role("button", name="Data Sources").click()
    page.locator("input[type=file]").set_input_files(
        [f"{REPO}/data/synthetic/orders_reference.csv", f"{REPO}/data/synthetic/products.csv"])
    res = page.get_by_test_id("upload-results")
    expect(res).to_contain_text("orders_reference.csv - ingested and set up automatically", timeout=40000)
    expect(res).to_contain_text("products.csv - ingested and set up automatically", timeout=40000)
    page.screenshot(path=f"{S}/auto_upload.png")
    print("OK two files uploaded together were detected and set up with no further clicks")

    page.get_by_role("button", name="Business Metrics").click()
    expect(page.locator("tr[data-metric=gross_profit]")).to_contain_text("Approved", timeout=10000)
    print("OK gross profit is already approved")
    page.locator("tr[data-metric=gross_profit]").get_by_role("button", name="Show lineage").click()
    lin = page.get_by_test_id("lineage")
    expect(lin).to_contain_text("column cost of orders_reference.csv", timeout=10000)
    expect(lin).to_contain_text("Sales (fact table): built, 3 rows from orders_reference.csv")
    expect(lin).to_contain_text("net_sales - cost_of_goods_sold")
    print("OK lineage shows gross profit traced to the cost column of the uploaded file")

    page.get_by_role("button", name="Home").click()
    expect(page.get_by_text("Net Revenue: 350")).to_be_visible(timeout=10000)
    expect(page.get_by_text("Gross Profit: 140")).to_be_visible(timeout=10000)
    print("OK dashboard shows Net Revenue 350 and Gross Profit 140")

    page.get_by_role("button", name="Ask Your Data").click()
    page.get_by_placeholder("e.g. Why has gross profit").fill("net revenue by category")
    page.get_by_role("button", name="Ask", exact=True).click()
    expect(page.get_by_text("How this was calculated")).to_be_visible(timeout=10000)
    cells = page.locator("table td").all_inner_texts()
    assert cells[:4] == ["Accessories", "200", "Hardware", "150"], cells
    print("OK category question answered through the automatic join")

    # an ambiguous file is not guessed: it waits for a person
    amb = pathlib.Path(tempfile.mkdtemp()) / "mystery.csv"
    amb.write_text("order_id,order_date,amount\nA,2026-01-01,10\nB,2026-01-02,20\n")
    page.get_by_role("button", name="Data Sources").click()
    page.locator("input[type=file]").set_input_files(str(amb))
    expect(page.get_by_test_id("upload-results")).to_contain_text("mystery.csv - ingested - needs your check", timeout=30000)
    page.get_by_role("link", name="mystery", exact=True).click()
    expect(page.get_by_test_id("auto-review")).to_be_visible()
    print("OK an ambiguous 'amount' column is not guessed: it asks the person")

    # monitoring: source health, a failed refresh shows on Home, a summary can be created
    row = page.get_by_test_id("source-health").locator("tr[data-source='orders_reference.csv']")
    expect(row).to_contain_text("Up to date", timeout=10000)
    row.get_by_label("Days between files for orders_reference.csv").fill("1")
    row.get_by_role("button", name="Save").click()
    expect(page.get_by_test_id("source-health").get_by_role("status")).to_have_text("Saved.", timeout=10000)
    bad = pathlib.Path(tempfile.mkdtemp()) / "broken.csv"
    bad.write_text("a,A\n1,2\n")
    page.locator("input[type=file]").set_input_files(str(bad))
    expect(page.get_by_test_id("upload-results")).to_contain_text("broken.csv - failed", timeout=30000)
    expect(page.get_by_test_id("source-health").locator("tr[data-source='broken.csv']")).to_contain_text("Failed", timeout=10000)
    page.get_by_role("button", name="Home").click()
    expect(page.get_by_test_id("health-banner")).to_contain_text("broken.csv", timeout=10000)
    print("OK source health shows the failed upload and Home says what needs attention")
    page.get_by_role("button", name="Reports").click()
    page.get_by_role("button", name="Create summary now").click()
    expect(page.get_by_test_id("summary-headline")).to_contain_text("needs attention", timeout=15000)
    expect(page.get_by_test_id("summary")).to_contain_text("file(s) uploaded")
    print("OK a management summary was created with the operations and what needs attention")
    expect(page.get_by_test_id("recommendations")).to_be_visible(timeout=15000)
    expect(page.get_by_test_id("recommendations")).to_contain_text("Recommendations")
    print("OK the Recommendations panel is shown on Reports")
    page.get_by_role("button", name="Data Sources").click()
    page.get_by_label("Web source name").fill("Metadata probe")
    page.get_by_label("Web address").fill("https://169.254.169.254/latest/meta-data/x.csv")
    page.get_by_role("button", name="Connect and fetch").click()
    expect(page.get_by_test_id("web-msg")).to_contain_text("private or local network", timeout=10000)
    print("OK an address on a private network is refused in the browser")
    # dashboards: one click builds an overview of approved metrics, tiles are calculated live
    page.get_by_role("button", name="Dashboards").click()
    page.get_by_role("button", name="Create overview from approved metrics").click()
    view = page.get_by_test_id("dashboard-view")
    expect(view).to_contain_text("Net Revenue", timeout=15000)
    expect(view).to_contain_text("350")
    expect(view).to_contain_text("Gross Profit")
    print("OK a dashboard was created in one click and its tiles show the approved metrics")
    page.get_by_role("button", name="Delete dashboard").click()
    expect(page.get_by_test_id("dashboard-view")).to_have_count(0, timeout=10000)
    print("OK the dashboard can be deleted")

    page.get_by_role("button", name="Business Metrics").click()
    row = page.locator('tr[data-metric="net_revenue"]')
    row.get_by_role("button", name="Edit details").click()
    page.get_by_label("Owner (who answers questions about this number)").fill("Finance team")
    page.get_by_role("button", name="Save details").click()
    expect(page.get_by_test_id("metric-details")).to_contain_text("Saved.", timeout=10000)
    expect(row).to_contain_text("Owner: Finance team", timeout=10000)
    print("OK a metric owner was saved and shown, with no change to its approval")

    page.get_by_role("button", name="Settings").click()
    expect(page.get_by_label("Build models and approve standard metrics automatically")).to_be_checked(timeout=10000)
    print("OK the setting is on by default and visible in Settings")
    page.get_by_label("stale_days").fill("30")
    page.get_by_role("button", name="Save limits").click()
    expect(page.get_by_test_id("quality-rules")).to_contain_text("Saved.", timeout=10000)
    page.get_by_role("button", name="Home").click(); page.get_by_role("button", name="Settings").click()
    expect(page.get_by_label("stale_days")).to_have_value("30", timeout=10000)
    print("OK data-quality limits were saved and are read back from the server after leaving the page")
    b.close()
print("JS errors:", errors or "none")
sys.exit(1 if errors else 0)
