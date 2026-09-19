"""End-to-end run of a fresh installation, in Chromium.

Setup wizard -> Authelia sign-in -> admin panel (every tab, a write, per-browser
language) -> DICOM upload -> DICOMweb query and frame -> OHIF viewer -> Orthanc
Explorer 2 menu -> share page. Prints one line per check and exits non-zero if
any failed.

Run by tests/e2e/run.sh inside the Playwright image, network host:
    python browser.py            (E2E_URL defaults to https://pacs.localhost:30443)
"""
import io
import os
import re
import secrets
import string
import sys

from playwright.sync_api import sync_playwright
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

URL = os.environ.get("E2E_URL", "https://pacs.localhost:30443")
PASSWORD = os.environ.get("E2E_PASSWORD") or "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
EMAIL = os.environ.get("E2E_EMAIL", "admin-e2e@example.org")
USERS_TAB = {"fr": "Utilisateurs", "en": "Users"}
MENU = {"fr": ("Partages", "Administration", "Déconnexion"), "en": ("Shares", "Administration", "Sign out")}
SHARES_TITLE = {"fr": "Partages", "en": "Shares"}
UNKNOWN_ACCOUNT = {"fr": "Compte inconnu.", "en": "Unknown account."}

failures = 0


def check(name, condition, extra=""):
    global failures
    failures += 0 if condition else 1
    print(f"  {'OK  ' if condition else 'FAIL'} {name} {extra}".rstrip(), flush=True)
    if not condition and os.environ.get("GITHUB_ACTIONS"):
        # Public on GitHub, unlike the job log.
        print(f"::error title=e2e check failed::{name} {extra}".replace("\n", " ")[:900], flush=True)


def dicom_bytes():
    """A small, valid Secondary Capture image: 64x64, 8 bits, a gradient."""
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID, ds.SeriesInstanceUID = generate_uid(), generate_uid()
    ds.PatientName, ds.PatientID = "E2E^Test", "E2E0001"
    ds.StudyDate, ds.Modality, ds.StudyDescription = "20260919", "OT", "End-to-end test"
    ds.SeriesNumber, ds.InstanceNumber = 1, 1
    ds.Rows = ds.Columns = 64
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated = ds.BitsStored = 8
    ds.HighBit, ds.PixelRepresentation = 7, 0
    ds.PixelData = bytes((x * 4) % 256 for x in range(64 * 64))
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    return buf.getvalue(), ds.StudyInstanceUID, ds.SeriesInstanceUID, ds.SOPInstanceUID


def watch_errors(page, allowed):
    """Collect JS errors and console errors, minus the ones a step expects."""
    errors = []
    page.on("pageerror", lambda e: errors.append(f"JS {page.url[:50]}: {e}"[:200]))
    page.on("console", lambda m: errors.append(f"console {page.url[:50]}: {m.text}"[:200])
            if m.type == "error" and not any(a(page.url, m.text) for a in allowed) else None)
    return errors


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--host-resolver-rules=MAP *.localhost 127.0.0.1"])

    # A French browser: the wizard must open in French.
    ctx = browser.new_context(ignore_https_errors=True, locale="fr-FR")
    page = ctx.new_page()
    expected_404 = [False]
    errors = watch_errors(page, [
        lambda url, text: "/auth/setup" in url and re.search(r"status of (422|409)", text),
        lambda url, text: "/auth/admin" in url and "status of 404" in text and expected_404[0],
    ])

    print("-- setup wizard")
    page.goto(URL + "/auth/setup")
    check("opens in the browser's language (fr-FR)", page.inner_text("h1").strip() == "Configuration initiale")
    buttons = page.eval_on_selector_all("[data-langue]", "els => els.map(e => e.dataset.langue)")
    check("language buttons come from the translation files", sorted(buttons) == ["en", "fr"], str(buttons))
    page.click("button[data-langue=en]")
    check("switches to English", page.evaluate("document.documentElement.lang") == "en")
    page.fill("#email", "admin@" + re.sub(r"^https://([^:/]+).*", r"\1", URL))
    page.fill("#displayname", "Admin E2E")
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    page.click("#submit-btn")
    page.wait_for_selector(".msg--err", timeout=10000)
    check("a .localhost address is refused with a clear message", ".localhost" in page.inner_text("#msg"))
    page.fill("#email", EMAIL)
    page.click("#submit-btn")
    page.wait_for_selector(".msg--ok", timeout=20000)
    check("administrator created, setup finalised", True)

    print("-- Authelia sign-in")
    page.wait_for_selector("#username-textfield", timeout=30000)
    page.fill("#username-textfield", EMAIL)
    page.fill("#password-textfield", PASSWORD)
    page.click("#sign-in-button")
    page.wait_for_url(re.compile(r".*/auth/admin.*"), timeout=30000)
    page.wait_for_load_state("networkidle")
    check("sign-in with the e-mail address lands on the panel", "/auth/admin" in page.url)

    print("-- admin panel, in the browser's language (fr)")
    check("page language", page.evaluate("document.documentElement.lang") == "fr")
    check("users tab translated", USERS_TAB["fr"].upper() in page.inner_text(".admin-tabs").upper())
    content = page.content()
    check("no leftover [[token]] or {placeholder}", "[[" not in content and "{admin_" not in content)
    check("administrator listed, bootstrap account gone", EMAIL in content and "bootstrap@localhost" not in content)
    for tab in ("orthanc", "modalities", "cf", "session", "backups", "audit", "health", "users"):
        page.click(f".admin-tab[data-tab={tab}]")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(400)
        text = page.inner_text(f"#panel-{tab}")
        check(f"tab {tab}", page.locator(f"#panel-{tab}").is_visible()
              and "[[" not in text and "undefined" not in text and "[object Object]" not in text)
    page.click(".admin-tab[data-tab=backups]")
    page.wait_for_timeout(800)
    check("backups tab does not show the bootstrap account", "bootstrap@localhost" not in page.inner_text("#panel-backups"))
    page.click(".admin-tab[data-tab=cf]")
    page.wait_for_timeout(800)
    check("Cloudflare tab: not configured is shown as information, not as an alarm",
          page.locator("#cf-status .msg--info").count() == 1 and page.locator("#cf-status .msg--err").count() == 0)

    print("-- a write from the panel: create then delete an account")
    page.click(".admin-tab[data-tab=users]")
    page.click("#panel-users summary")
    page.fill("#add-user-form [name=email]", "doctor-e2e@example.org")
    page.fill("#add-user-form [name=displayname]", "Doctor E2E")
    page.fill("#add-user-form [name=password]", "ALongPasswordForE2E2026")
    page.click("#add-user-form button[type=submit]")
    page.wait_for_selector("#global-msg.msg--ok, #global-msg.msg--err", timeout=15000)
    check("account created from the form", page.locator("#global-msg.msg--ok").count() == 1,
          page.inner_text("#global-msg")[:90])
    page.wait_for_function("document.querySelector('#users-table').innerText.includes('doctor-e2e@example.org')",
                           timeout=10000)
    page.click("tr:has-text('doctor-e2e@example.org') button.oe2-btn--danger")
    page.wait_for_selector("#confirm-backdrop:not([hidden])", timeout=5000)
    page.click("#confirm-ok")
    page.wait_for_function("!document.querySelector('#users-table').innerText.includes('doctor-e2e@example.org')",
                           timeout=10000)
    check("account deleted after confirmation", True)

    print("-- language chosen in the panel, for this browser only (en)")
    with page.expect_navigation(timeout=15000):
        page.select_option("#langue-select", "en")
    page.wait_for_load_state("networkidle")
    check("panel switches to English", page.evaluate("document.documentElement.lang") == "en"
          and USERS_TAB["en"].upper() in page.inner_text(".admin-tabs").upper())
    expected_404[0] = True
    status, detail = page.evaluate("""async () => {
        const r = await fetch('/api/admin/users/unknown@example.org', {method: 'DELETE',
            credentials: 'same-origin', headers: {'x-csrf-token': window.__CSRF__}});
        return [r.status, (await r.json()).detail]; }""")
    page.wait_for_timeout(300)
    expected_404[0] = False
    check("server messages follow the choice", detail == UNKNOWN_ACCOUNT["en"], f"{status} {detail}")

    print("-- DICOM: upload, DICOMweb, OHIF")
    data, study, series, instance = dicom_bytes()
    r = page.request.post(URL + "/instances", data=data, headers={"Content-Type": "application/dicom"})
    check("upload through the signed-in session", r.status == 200, f"HTTP {r.status}")
    r = page.request.get(URL + "/dicom-web/studies?PatientID=E2E0001", headers={"Accept": "application/dicom+json"})
    found = r.status == 200 and study in r.text()
    check("the study is listed by DICOMweb (QIDO)", found, f"HTTP {r.status}")
    r = page.request.get(f"{URL}/dicom-web/studies/{study}/series/{series}/instances/{instance}/frames/1",
                         headers={"Accept": "multipart/related; type=application/octet-stream"})
    check("its pixels are served (WADO-RS frame)", r.status == 200 and len(r.body()) > 64 * 64, f"HTTP {r.status}")
    viewer = ctx.new_page()
    viewer_errors = watch_errors(viewer, [])
    viewer.goto(f"{URL}/ohif/viewer?StudyInstanceUIDs={study}")
    try:
        viewer.wait_for_selector("canvas", timeout=45000)
        viewer.wait_for_timeout(3000)
        drawn = True
    except Exception:  # noqa: BLE001 -- reported as a failed check
        drawn = False
    check("OHIF opens the study and draws it", drawn)
    check("OHIF shows no error banner", "An error occurred" not in viewer.content())
    viewer.close()

    print("-- Orthanc Explorer 2 and shares (en, from the panel choice)")
    page.goto(URL + "/ui/app/")
    page.wait_for_selector("#admin-injected", timeout=30000)
    page.wait_for_timeout(1500)
    check("OE2 menu: shares", page.inner_text("#shares-injected").strip() == MENU["en"][0])
    check("OE2 menu: administration", page.inner_text("#admin-injected").strip() == MENU["en"][1])
    check("OE2 menu: sign out", page.inner_text("#logout-fixe").strip() == MENU["en"][2])
    page.goto(URL + "/auth/tokens/manage")
    page.wait_for_load_state("networkidle")
    check("share page", SHARES_TITLE["en"] in page.title(), page.title())
    check("no JavaScript or console error", not errors, " | ".join(errors[:4]))
    check("no JavaScript error in OHIF", not [e for e in viewer_errors if e.startswith("JS")],
          " | ".join(viewer_errors[:3]))

    # A German browser, no translation: the installation default applies,
    # English here because the wizard was completed in English.
    print("-- browser without a translation (de-DE)")
    other = browser.new_context(ignore_https_errors=True, locale="de-DE",
                                storage_state=ctx.storage_state())
    other.clear_cookies(name="orthanc_lang")
    other.clear_cookies(name="i18next")
    page2 = other.new_page()
    page2.goto(URL + "/auth/admin")
    page2.wait_for_load_state("networkidle")
    check("falls back to the installation default (en)", page2.evaluate("document.documentElement.lang") == "en")
    browser.close()

print(f"== {'ALL CHECKS PASSED' if not failures else str(failures) + ' FAILED'} ==")
sys.exit(1 if failures else 0)
