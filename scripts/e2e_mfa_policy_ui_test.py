"""Browser test: the organisation policy "require two-step sign-in". Needs API and web running:
    python scripts/e2e_mfa_policy_ui_test.py
An admin sets up two-step and turns the policy on; a colleague signs in and is held on the set-up screen until they
enrol; the admin resets the colleague's two-step (lost phone) and the colleague is sent back to set-up."""
import base64, hashlib, hmac, json, os, struct, sys, time as _t, urllib.request
from playwright.sync_api import sync_playwright, expect

STAMP = int(_t.time())
ADMIN, ANA, PW = f"adm{STAMP}@test.example", f"ana{STAMP}@test.example", "correct-horse-1"


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


def sign_in(page, email, password=PW, code=None):
    page.goto("http://localhost:3000")
    page.get_by_role("link", name="Sign in").click()
    page.get_by_placeholder("Email").fill(email)
    page.get_by_placeholder("Password (10+ chars)").fill(password)
    page.get_by_role("button", name="Sign in").click()
    if code:
        expect(page.get_by_text("Enter the 6-digit code")).to_be_visible(timeout=10000)
        page.get_by_placeholder("Authenticator or recovery code").fill(code)
        page.get_by_role("button", name="Sign in").click()


def enrol(page):
    page.get_by_role("button", name="Set up two-step sign-in").click()
    secret = page.get_by_test_id("mfa-secret").inner_text()
    page.get_by_placeholder("6-digit code").fill(totp(secret))
    page.get_by_role("button", name="Turn on").click()
    expect(page.get_by_test_id("recovery-codes")).to_be_visible(timeout=10000)
    codes = [c.strip() for c in page.get_by_test_id("recovery-codes").inner_text().split("\n") if c.strip()]
    return secret, codes


errors = []
with sync_playwright() as p:
    kw = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}
    b = p.chromium.launch(args=["--no-sandbox"], **kw)
    admin = b.new_page(viewport={"width": 1100, "height": 900})
    admin.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    admin.goto("http://localhost:3000")
    admin.get_by_placeholder("Organisation").fill("Policy Ltd")
    admin.get_by_placeholder("Email").fill(ADMIN)
    admin.get_by_placeholder("Password (10+ chars)").fill(PW)
    admin.get_by_role("button", name="Create workspace").click()
    admin.get_by_role("button", name="Settings").click()

    box = admin.get_by_label("Require two-step sign-in for everyone")
    expect(box).not_to_be_checked(timeout=10000)
    box.check()
    admin.get_by_role("button", name="Save settings").click()
    expect(admin.get_by_text("Turn on two-step sign-in for your own account first")).to_be_visible(timeout=10000)
    print("OK the policy cannot be switched on before the admin has two-step: no self-lockout")

    admin_secret, admin_codes = enrol(admin)
    admin.get_by_role("button", name="Save settings").click()
    expect(admin.get_by_role("status").filter(has_text="Saved.")).to_be_visible(timeout=10000)
    expect(admin.get_by_test_id("mfa-required-note")).to_be_visible(timeout=10000)
    print("OK admin turned on two-step, then the organisation policy")

    tok = api("POST", "/api/v1/auth/login", {"email": ADMIN, "password": PW, "code": totp(admin_secret, 1)})["token"]
    api("POST", "/api/v1/users", {"email": ANA, "role": "analyst", "password": "initial-pass-2"}, tok)

    ana = b.new_page(viewport={"width": 1100, "height": 900})
    ana.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    sign_in(ana, ANA, "initial-pass-2")
    expect(ana.get_by_test_id("mfa-enrol")).to_contain_text("Set up two-step sign-in to continue", timeout=15000)
    expect(ana.get_by_role("button", name="Data Sources")).to_have_count(0)
    print("OK a colleague without two-step is held on the set-up screen (no data screens)")

    enrol(ana)
    ana.get_by_role("button", name="I have saved my recovery codes - continue").click()
    expect(ana.get_by_role("button", name="Data Sources")).to_be_visible(timeout=10000)
    print("OK after enrolling the colleague continues into the app")

    # lost phone: admin resets, the colleague's next request sends them back to set-up
    admin.reload()
    admin.get_by_role("link", name="Sign in").click()
    admin.get_by_placeholder("Email").fill(ADMIN)
    admin.get_by_placeholder("Password (10+ chars)").fill(PW)
    admin.get_by_role("button", name="Sign in").click()
    admin.get_by_placeholder("Authenticator or recovery code").fill(admin_codes[-1])
    admin.get_by_role("button", name="Sign in").click()
    admin.get_by_role("button", name="Settings").click()
    row = admin.locator(f"tr[data-user='{ANA}']")
    row.get_by_role("button", name="Reset two-step").click()
    expect(row).to_contain_text("off", timeout=10000)
    ana.get_by_role("button", name="Business Metrics").click()
    expect(ana.get_by_test_id("mfa-enrol")).to_be_visible(timeout=10000)
    print("OK admin reset the colleague's two-step; they are sent back to set-up on their next action")
    b.close()
print("JS errors:", errors or "none")
sys.exit(1 if errors else 0)
