"""Browser test of insights and driver analysis (server default AUTO_MODEL_DEFAULT=true). Needs API, worker, web running.
Uploads six months of orders where, in June, one product's profit falls by 60, then checks the Home insight, the answer to
"Why has gross profit fallen?" (the product that moved, the unfinished month excluded, caveats), saving it as a report and
re-running it from Reports."""
import os, pathlib, sys, tempfile, time
from playwright.sync_api import sync_playwright, expect

S = sys.argv[1] if len(sys.argv) > 1 else "."
EMAIL, PW = f"ins{int(time.time())}@test.example", "correct-horse-1"
rows, n = ["order_id,order_date,net_sales,cost,product"], 0
for mo in range(1, 6):
    for prod, sales, cost in (("Widget", 100, 60), ("Gadget", 200, 130)):
        n += 1
        rows.append(f"O{n},2026-{mo:02d}-10,{sales},{cost},{prod}")
rows += ["J1,2026-06-10,100,60,Widget", "J2,2026-06-12,100,90,Gadget", "L1,2026-07-03,500,100,Widget"]
f = pathlib.Path(tempfile.mkdtemp()) / "orders_months.csv"
f.write_text("\n".join(rows) + "\n")

errors = []
with sync_playwright() as p:
    kw = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}
    b = p.chromium.launch(args=["--no-sandbox"], **kw)
    page = b.new_page(viewport={"width": 1100, "height": 900})
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto("http://localhost:3000")
    page.get_by_placeholder("Organisation").fill("Insights Ltd")
    page.get_by_placeholder("Email").fill(EMAIL)
    page.get_by_placeholder("Password (10+ chars)").fill(PW)
    page.get_by_role("button", name="Create workspace").click()
    page.get_by_role("button", name="Data Sources").click()
    page.locator("input[type=file]").set_input_files(str(f))
    expect(page.get_by_test_id("upload-results")).to_contain_text("ingested and set up automatically", timeout=40000)

    page.get_by_role("button", name="Home").click()
    expect(page.get_by_test_id("insights")).to_contain_text("Gross Profit fell by 60.00", timeout=15000)
    page.screenshot(path=f"{S}/insights_home.png")
    print("OK Home shows the automatic insight: gross profit fell by 60.00")

    page.get_by_role("button", name="Ask Your Data").click()
    page.get_by_placeholder("e.g. Why has gross profit").fill("Why has gross profit fallen?")
    page.get_by_role("button", name="Ask", exact=True).click()
    expect(page.get_by_test_id("explanation-headline")).to_contain_text("fell by 60.00 (54.5%) in 2026-06 compared with 2026-05", timeout=15000)
    t = page.get_by_test_id("drivers-product")
    expect(t).to_contain_text("Gadget")
    expect(t.locator("tr", has_text="Gadget")).to_contain_text("-60.00")
    expect(t.locator("tr", has_text="Gadget")).to_contain_text("100%")
    expect(page.get_by_text("2026-07 is still in progress")).to_be_visible()
    expect(page.get_by_text("not why it happened")).to_be_visible()
    page.screenshot(path=f"{S}/insights_why.png")
    print("OK 'why' answer: Gadget -60.00 is 100% of the change; unfinished month excluded; caveats shown")

    page.get_by_role("button", name="Save as report").click()
    expect(page.get_by_text("Saved under Reports.")).to_be_visible(timeout=10000)
    page.get_by_role("button", name="Reports").click()
    page.get_by_role("button", name="Run").first.click()
    expect(page.get_by_test_id("explanation-headline")).to_contain_text("fell by 60.00", timeout=15000)
    print("OK the 'why' question was saved as a report and recomputed from Reports")

    page.get_by_role("button", name="Ask Your Data").click()
    page.get_by_placeholder("e.g. Why has gross profit").fill("Why did revenue drop last month?")
    page.get_by_role("button", name="Ask", exact=True).click()
    expect(page.get_by_test_id("explanation-headline")).to_contain_text("Net Revenue fell by 100.00", timeout=15000)
    print("OK plain-English 'drop' is not mistaken for SQL")
    b.close()
print("JS errors:", errors or "none")
sys.exit(1 if errors else 0)
