"""Changing the public address after installation, as an administrator does it.

    python move.py change   sign in at E2E_URL, ask the panel to move to E2E_NEW_URL
    python move.py verify   after apply.sh: sign in at E2E_NEW_URL and check the PACS,
                            the study uploaded by browser.py included

Run by tests/e2e/run.sh, which runs apply.sh between the two.
"""
import os
import re
import sys

from playwright.sync_api import sync_playwright

OLD = os.environ["E2E_URL"]
NEW = os.environ["E2E_NEW_URL"]
EMAIL = os.environ["E2E_EMAIL"]
PASSWORD = os.environ["E2E_PASSWORD"]
failures = 0


def check(name, condition, extra=""):
    global failures
    failures += 0 if condition else 1
    print(f"  {'OK  ' if condition else 'FAIL'} {name} {extra}".rstrip(), flush=True)
    if not condition and os.environ.get("GITHUB_ACTIONS"):
        print(f"::error title=e2e check failed::{name} {extra}".replace("\n", " ")[:900], flush=True)


def sign_in(page, base):
    page.goto(base + "/auth/admin")
    page.wait_for_selector("#username-textfield", timeout=30000)
    page.fill("#username-textfield", EMAIL)
    page.fill("#password-textfield", PASSWORD)
    page.click("#sign-in-button")
    page.wait_for_url(re.compile(r".*/auth/admin.*"), timeout=30000)
    page.wait_for_load_state("networkidle")


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])
    ctx = browser.new_context(ignore_https_errors=True, locale="en-US")
    page = ctx.new_page()

    if sys.argv[1] == "change":
        print(f"-- panel: public address {OLD} -> {NEW}")
        sign_in(page, OLD)
        status, body = page.evaluate("""async (url) => {
            const r = await fetch('/api/admin/network', {method: 'POST', credentials: 'same-origin',
                headers: {'content-type': 'application/json', 'x-csrf-token': window.__CSRF__},
                body: JSON.stringify({public_url: url})});
            return [r.status, await r.json()]; }""", NEW)
        check("the panel accepts the new address", status == 200 and body.get("restart_required") is True,
              f"{status} {str(body)[:160]}")
    else:
        print(f"-- the PACS at its new address {NEW}")
        sign_in(page, NEW)
        check("sign-in at the new address lands on the panel", "/auth/admin" in page.url, page.url)
        r = page.request.get(NEW + "/dicom-web/studies?PatientID=E2E0001", headers={"Accept": "application/dicom+json"})
        check("the study uploaded before the move is still there", r.status == 200 and "E2E0001" in r.text(),
              f"HTTP {r.status}")
        page.goto(NEW + "/ui/app/")
        page.wait_for_selector("#admin-injected", timeout=30000)
        check("Orthanc Explorer 2 and its menu", page.locator("#admin-injected").count() == 1)
        if re.sub(r"^https://[^:/]+", "", OLD) != re.sub(r"^https://[^:/]+", "", NEW):
            try:
                page.request.get(OLD + "/auth/", timeout=5000)
                closed = False
            except Exception:  # noqa: BLE001 -- a refused connection is the expected outcome
                closed = True
            check("the old port no longer answers", closed)

    browser.close()

print(f"== {'ALL CHECKS PASSED' if not failures else str(failures) + ' FAILED'} ==")
sys.exit(1 if failures else 0)
