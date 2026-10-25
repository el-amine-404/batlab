#!/usr/bin/env python3
"""M5 slice 3: the real web UI in headless Firefox, desktop and iPhone sizes.

Usage: ui_test.py BASE_URL ADMIN_TOKEN FIXTURE_PDF SCREENSHOT_DIR
Expects the stack from test_m5_ui.sh (two released documents, one held
document whose display name is an XSS payload).
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, UnexpectedAlertPresentException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.ui import WebDriverWait

BASE, TOKEN, FIXTURE, SHOTS = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
HOSTILE = "<img src=x onerror=alert(1)>.pdf"


def check(name, condition, detail=""):
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    print(f"ok - {name}")


def browser(width, height):
    options = webdriver.FirefoxOptions()
    options.add_argument("-headless")
    driver = webdriver.Firefox(options=options)
    driver.set_window_rect(0, 0, width, height)
    # Inner viewport must match the target device width for layout checks.
    inner = driver.execute_script("return window.innerWidth")
    driver.set_window_rect(0, 0, width + (width - inner), height)
    return driver


def wait(driver, condition, seconds=20):
    return WebDriverWait(driver, seconds).until(condition)


def text_present(driver, text, seconds=20):
    wait(driver, lambda d: text in d.find_element(By.TAG_NAME, "body").text, seconds)


def login(driver):
    driver.get(BASE + "/")
    field = wait(driver, ec.presence_of_element_located((By.NAME, "token")))
    field.send_keys(TOKEN)
    driver.find_element(By.CSS_SELECTOR, "button[type=submit]").click()
    text_present(driver, "Upload PDFs")


# --- Static shell headers -------------------------------------------------------
with urllib.request.urlopen(BASE + "/") as response:
    headers = {key.lower(): value for key, value in response.headers.items()}
csp = headers.get("content-security-policy", "")
check("CSP forbids inline script and framing", "script-src 'self'" in csp and "'unsafe-inline'" not in csp
      and "frame-ancestors 'none'" in csp and "default-src 'none'" in csp, csp)
check("security headers", headers.get("x-content-type-options") == "nosniff" and headers.get("referrer-policy") == "no-referrer"
      and "server" not in headers, headers)

# --- Desktop ---------------------------------------------------------------------
driver = browser(1280, 800)
try:
    login(driver)
    check("CSS applied under CSP", driver.execute_script(
        "return getComputedStyle(document.body).backgroundColor") not in ("rgba(0, 0, 0, 0)", "transparent"))
    check("API token not kept in browser storage", driver.execute_script(
        "return localStorage.length + sessionStorage.length") == 0 and TOKEN not in driver.page_source)
    check("session cookie not readable by scripts", driver.execute_script("return document.cookie") == "")

    # Jobs: the hostile display name is shown as text and never interpreted.
    driver.get(BASE + "/jobs")
    text_present(driver, HOSTILE)
    jobs = driver.find_element(By.CSS_SELECTOR, "[data-testid=job-list]")
    check("XSS payload rendered as text, no element created", len(jobs.find_elements(By.TAG_NAME, "img")) == 0)
    check("released documents listed", "Ready" in jobs.text and "Needs review" in jobs.text)
    driver.save_screenshot(str(SHOTS / "desktop-jobs.png"))

    # Upload through the real file input; the server accepts and queues it.
    driver.get(BASE + "/upload")
    wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=file-input]"))).send_keys(FIXTURE)
    wait(driver, lambda d: "Upload 1" in d.find_element(By.TAG_NAME, "body").text)
    next(button for button in driver.find_elements(By.TAG_NAME, "button") if button.text.startswith("Upload")).click()
    link = wait(driver, ec.element_to_be_clickable((By.LINK_TEXT, "View progress")))
    link.click()
    text_present(driver, "invoice-en.pdf")
    # No workers run in this stack, so the new upload waits in the queue (live status shown).
    text_present(driver, "Queued")
    check("upload accepted and tracked", "/scans/scan-" in driver.current_url)

    # Released scan: findings, final checks and the archive link.
    driver.get(BASE + "/scans/scan-01J00000000000000000000060")
    text_present(driver, "Final check 1")
    check("scan detail shows PASS and archive link", "PASS" in driver.find_element(By.TAG_NAME, "body").text
          and driver.find_element(By.LINK_TEXT, "open document"))
    driver.find_element(By.LINK_TEXT, "open document").click()

    # Archive document: downloads are attachment links to the approved file only.
    title = wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=document-title]")))
    check("archived invoice title", title.text == "Internet Invoice - October 2026", title.text)
    download = driver.find_element(By.CSS_SELECTOR, "[data-testid=download-pdf]")
    check("download is an attachment link to the archive file", download.get_attribute("download") is not None
          and download.get_attribute("href").endswith("/file") and "/quarantine" not in download.get_attribute("href"))
    driver.save_screenshot(str(SHOTS / "desktop-document.png"))

    # Edit tags (If-Match CAS via the archive worker); the PDF stays unchanged.
    fields = wait(driver, lambda d: d.find_elements(By.CSS_SELECTOR, "form input") or False)
    fields[1].clear()
    fields[1].send_keys("finance, internet, invoice, paid")
    driver.find_element(By.XPATH, "//button[text()='Save']").click()
    text_present(driver, "Saved (revision 2)", 30)
    driver.refresh()
    wait(driver, lambda d: d.find_elements(By.CSS_SELECTOR, "form input") and
         d.find_elements(By.CSS_SELECTOR, "form input")[1].get_attribute("value") == "finance, internet, invoice, paid")
    check("tag edit persisted", True)

    # Archive browser groups by folder.
    driver.get(BASE + "/archive")
    # Folder headings are uppercased by CSS; Selenium reports rendered text.
    wait(driver, lambda d: "finance / 2026 / invoices" in d.find_element(By.TAG_NAME, "body").text.lower())
    check("archive grouped by folder", "unsorted / 2026" in driver.find_element(By.TAG_NAME, "body").text.lower())

    # Quarantine: held item, admin delete with confirmation.
    driver.get(BASE + "/quarantine")
    text_present(driver, HOSTILE)
    driver.find_element(By.XPATH, "//button[text()='Delete']").click()
    wait(driver, ec.alert_is_present()).accept()
    text_present(driver, "Nothing is held.")
    check("held original deleted from the UI", True)

    # Status page renders real backend data.
    driver.get(BASE + "/dashboard")
    text_present(driver, "Database")
    check("health shows database and broker up", driver.find_element(By.CSS_SELECTOR, "[data-testid=health]").text.count("UP") >= 2)
    wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=ai]")))
    check("AI card shows enrichment is not configured", "Not configured" in driver.find_element(By.CSS_SELECTOR, "[data-testid=ai]").text)
    driver.save_screenshot(str(SHOTS / "desktop-status.png"))

    # The service worker never caches API data.
    driver.get(BASE + "/jobs")
    text_present(driver, "Jobs")
    time.sleep(2)
    cached = driver.execute_async_script("""
        const done = arguments[arguments.length - 1];
        caches.keys().then(async (names) => {
          const urls = [];
          for (const name of names) for (const request of await (await caches.open(name)).keys()) urls.push(request.url);
          done(urls);
        }).catch(() => done(null));""")
    check("service worker cache holds no API responses", cached is not None and not any("/api/" in url for url in cached), cached)

    # Sign out ends the session server-side.
    driver.find_element(By.XPATH, "//button[text()='Sign out']").click()
    wait(driver, ec.presence_of_element_located((By.NAME, "token")))
    status = driver.execute_async_script("""const done = arguments[arguments.length - 1];
        fetch('/api/v1/scans', {credentials: 'same-origin'}).then(r => done(r.status));""")
    check("signed out: API refuses the old cookie", status == 401, status)
except UnexpectedAlertPresentException as alert:
    raise AssertionError(f"an unexpected alert fired (possible XSS): {alert}")
except Exception:
    # Synthetic fixture data only: show what the page displayed when a step failed.
    print("page at failure:", driver.current_url, "\n", driver.find_element(By.TAG_NAME, "body").text[:2000], file=sys.stderr)
    raise
finally:
    driver.quit()

# --- iPhone viewport (390x844 CSS px, 3x) -------------------------------------------
# Headless Firefox under WebDriver will not shrink below 500 px, so the phone layout
# is checked with Playwright's Firefox, which emulates the exact viewport.
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as playwright:
    phone_browser = playwright.firefox.launch()
    phone = phone_browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, has_touch=True,
                                      user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
    page = phone.new_page()
    page.on("dialog", lambda dialog: (_ for _ in ()).throw(AssertionError(f"unexpected dialog: {dialog.message}")))
    page.goto(BASE + "/")
    page.fill("input[name=token]", TOKEN)
    page.click("button[type=submit]")
    page.wait_for_selector("text=Upload PDFs")
    check("phone viewport is 390 CSS px", page.evaluate("window.innerWidth") == 390, page.evaluate("window.innerWidth"))
    check("mobile navigation shown, desktop navigation hidden",
          page.is_visible("nav[aria-label='Main mobile']") and not page.is_visible("nav[aria-label='Main']"))
    targets = {"upload": ("/upload", "Upload PDFs"), "jobs": ("/jobs", "invoice-en.pdf"), "archive": ("/archive", "Internet Invoice"),
               "document": ("/archive/doc-01J00000000000000000000060", "Download PDF"),
               "scan": ("/scans/scan-01J00000000000000000000060", "Final check"), "status": ("/dashboard", "System health")}
    for label, (path, marker) in targets.items():
        page.goto(BASE + path)
        page.wait_for_selector(f"text={marker}")
        width, scroll = page.evaluate("[document.documentElement.clientWidth, document.documentElement.scrollWidth]")
        check(f"iPhone {label}: no horizontal overflow", scroll <= width, f"scrollWidth {scroll} > {width}")
        page.screenshot(path=str(SHOTS / f"iphone-{label}.png"), full_page=True)
    heights = page.eval_on_selector_all("nav[aria-label='Main mobile'] a", "links => links.map(l => l.getBoundingClientRect().height)")
    check("mobile tap targets at least 44 px tall", heights and min(heights) >= 44, heights)
    # Tapping works with touch emulation: navigate via the bottom bar.
    page.tap("nav[aria-label='Main mobile'] >> text=Archive")
    page.wait_for_url(BASE + "/archive")
    check("bottom navigation tap", page.url.endswith("/archive"))
    phone_browser.close()

print("M5 UI tests passed")
