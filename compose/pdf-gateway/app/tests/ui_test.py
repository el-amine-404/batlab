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


def browser(width, height, dark=False):
    options = webdriver.FirefoxOptions()
    options.add_argument("-headless")
    if dark:
        # The system's own dark preference, as a dark desktop or phone sends it.
        options.set_preference("ui.systemUsesDarkTheme", 1)
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
    text_present(driver, "Add PDF files")


def body(driver):
    return driver.find_element(By.TAG_NAME, "body").text


def step_states(driver):
    return {item.get_attribute("data-step"): item.get_attribute("data-state")
            for item in driver.find_elements(By.CSS_SELECTOR, "[data-testid=steps] li")}


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
    # The theme switch stores only an explicit choice, and only that.
    driver.find_element(By.CSS_SELECTOR, "[data-testid=theme]").click()
    stored = driver.execute_script("return Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])")
    check("theme choice is the only thing stored", stored == [["pgw-theme", "light"]], stored)
    driver.find_element(By.CSS_SELECTOR, "[data-testid=theme]").click()
    check("dark theme applies", driver.execute_script("return document.documentElement.classList.contains('dark')"))
    driver.find_element(By.CSS_SELECTOR, "[data-testid=theme]").click()
    check("back to the system theme stores nothing", driver.execute_script("return localStorage.length") == 0)
    check("session cookie not readable by scripts", driver.execute_script("return document.cookie") == "")

    # Documents: one list for every upload; the hostile file name is shown as text, never interpreted.
    driver.get(BASE + "/documents")
    text_present(driver, HOSTILE)
    listing = driver.find_element(By.CSS_SELECTOR, "[data-testid=document-list]")
    check("XSS payload rendered as text, no element created", len(listing.find_elements(By.TAG_NAME, "img")) == 0)
    check("one list with ready and held files", "Ready" in listing.text and "Held" in listing.text)
    check("archived files show their title and folder", "Internet Invoice - October 2026" in listing.text
          and "Finance › Example Telecom" in listing.text, listing.text)
    driver.save_screenshot(str(SHOTS / "desktop-documents.png"))

    # Add files: choosing a file starts it at once (archive and Paperless, no options to pick).
    driver.get(BASE + "/")
    wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=file-input]"))).send_keys(FIXTURE)
    link = wait(driver, ec.element_to_be_clickable((By.LINK_TEXT, "Open")))
    link.click()
    text_present(driver, "invoice-en.pdf")
    # No workers run in this stack, so the new upload waits (live status shown in words).
    text_present(driver, "Checking your file")
    check("upload accepted and tracked on its document page", "/documents/scan-" in driver.current_url)
    text_present(driver, "After the checks.")
    check("a new upload is headed for Paperless", "Added to Paperless" in body(driver))

    # A released file: verdict, steps, clean copy, details, all on one page.
    driver.get(BASE + "/documents/scan-01J00000000000000000000060")
    title = wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=document-title]")))
    wait(driver, lambda d: d.find_elements(By.CSS_SELECTOR, "[data-testid=copy-fields]"))
    check("archived invoice title", title.text == "Internet Invoice - October 2026", title.text)
    check("verdict says the safe copy is ready", "Safe copy ready" in driver.find_element(By.CSS_SELECTOR, "[data-testid=verdict]").text)
    states = step_states(driver)
    check("every safety step done", all(states.get(key) == "done" for key in ("received", "scan", "signature", "rebuild", "recheck", "archive")), states)
    download = driver.find_element(By.CSS_SELECTOR, "[data-testid=download-pdf]")
    check("download is an attachment link to the archive file", download.get_attribute("download") is not None
          and download.get_attribute("href").endswith("/file") and "/quarantine" not in download.get_attribute("href"))
    driver.find_element(By.XPATH, "//summary[contains(., 'Technical details')]").click()
    text_present(driver, "Final check 1")
    check("technical details keep the final check", "PASS" in driver.find_element(By.CSS_SELECTOR, "[data-testid=checks]").text)
    driver.save_screenshot(str(SHOTS / "desktop-document.png"))

    # Archived without Paperless: add it from the page (no adapter in this stack: it stays on its way).
    wait(driver, ec.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid=send-to-paperless]"))).click()
    status = wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=paperless-status]")))
    check("adding to Paperless queues the copy", "Sending the clean copy" in status.text, status.text)
    check("the button is gone once requested", not driver.find_elements(By.CSS_SELECTOR, "[data-testid=send-to-paperless]"))

    # Edit tags (If-Match CAS via the archive worker); the PDF stays unchanged.
    driver.find_element(By.CSS_SELECTOR, "[data-testid=edit]").click()
    wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=edit-form]")))

    def field(label):
        return driver.find_element(By.XPATH, f"//form[@data-testid='edit-form']//label[span[text()='{label}']]//input")
    check("edit form offers every field", all(field(label) for label in ("Title", "From (correspondent)", "Date", "Number", "Amount", "Currency", "Tags"))
          and driver.find_elements(By.CSS_SELECTOR, "select[name=documentType]") and driver.find_elements(By.CSS_SELECTOR, "select[name=section]"))
    field("Tags").clear()
    field("Tags").send_keys("finance, internet, invoice, paid")
    field("Number").clear()
    field("Number").send_keys("2026-10-4471")
    driver.find_element(By.XPATH, "//button[text()='Save']").click()
    wait(driver, lambda d: "paid" in d.find_element(By.CSS_SELECTOR, "[data-testid=copy-fields]").text, 30)
    driver.refresh()
    wait(driver, lambda d: d.find_elements(By.CSS_SELECTOR, "[data-testid=copy-fields]")
         and "paid" in d.find_element(By.CSS_SELECTOR, "[data-testid=copy-fields]").text)
    check("tag and number edit persisted", "2026-10-4471" in driver.find_element(By.CSS_SELECTOR, "[data-testid=copy-fields]").text)
    check("edited by a person shows as filed by you", "Filed by you" in driver.find_element(By.CSS_SELECTOR, "[data-testid=filed-by]").text)
    view = driver.find_element(By.CSS_SELECTOR, "[data-testid=view-pdf]")
    check("view opens the clean copy inline in a new tab", view.get_attribute("href").endswith("/file?view=true")
          and view.get_attribute("target") == "_blank" and "/quarantine" not in view.get_attribute("href"))

    # Held files: the list's filter, then the admin deletes the original from its page.
    driver.get(BASE + "/documents?show=held")
    text_present(driver, HOSTILE)
    driver.find_element(By.PARTIAL_LINK_TEXT, HOSTILE).click()
    text_present(driver, "Held: not released")
    driver.find_element(By.XPATH, "//summary[contains(., 'Delete')]").click()
    driver.find_element(By.CSS_SELECTOR, "[data-testid=delete]").click()
    wait(driver, ec.alert_is_present()).accept()
    wait(driver, lambda d: d.current_url.endswith("/documents"))
    driver.get(BASE + "/documents?show=held")
    text_present(driver, "Nothing matches.")
    check("held document deleted everywhere from the UI", True)

    # Old addresses still lead to the right pages.
    driver.get(BASE + "/archive/doc-01J00000000000000000000060")
    wait(driver, lambda d: d.current_url.endswith("/documents/scan-01J00000000000000000000060"))
    check("old archive link redirects to the document", True)

    # System page renders real backend data, in words.
    driver.get(BASE + "/system")
    text_present(driver, "Is it working?")
    check("system says checking and archiving work", "Working." in driver.find_element(By.CSS_SELECTOR, "[data-testid=health]").text)
    check("AI line says it is not set up", "Not set up" in driver.find_element(By.CSS_SELECTOR, "[data-testid=ai]").text)
    driver.save_screenshot(str(SHOTS / "desktop-system.png"))

    # The service worker never caches API data.
    driver.get(BASE + "/documents")
    text_present(driver, "Documents")
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

# --- Dark system ---------------------------------------------------------------------
# A real Firefox told the system is dark (Playwright's colour-scheme emulation does
# not reach pages served with this CSP, so it can't stand in for a real device).
driver = browser(1280, 800, dark=True)
try:
    login(driver)
    driver.get(BASE + "/documents/scan-01J00000000000000000000060")
    wait(driver, lambda d: d.find_elements(By.CSS_SELECTOR, "[data-testid=download-pdf]"))
    background = driver.execute_script("return getComputedStyle(document.body).backgroundColor")
    check("dark system gives a dark page", driver.execute_script("return document.documentElement.classList.contains('dark')")
          and background != "oklch(0.984 0.003 247.858)", background)
    driver.save_screenshot(str(SHOTS / "desktop-document-dark.png"))
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
    page.wait_for_selector("text=Add PDF files")
    check("phone viewport is 390 CSS px", page.evaluate("window.innerWidth") == 390, page.evaluate("window.innerWidth"))
    check("mobile navigation shown, desktop navigation hidden",
          page.is_visible("nav[aria-label='Main mobile']") and not page.is_visible("nav[aria-label='Main']"))
    targets = {"add": ("/", "Add PDF files"), "documents": ("/documents", "invoice-en.pdf"),
               "document": ("/documents/scan-01J00000000000000000000060", "Download"), "system": ("/system", "Is it working?")}
    for label, (path, marker) in targets.items():
        page.goto(BASE + path)
        page.wait_for_selector(f"text={marker}")
        width, scroll = page.evaluate("[document.documentElement.clientWidth, document.documentElement.scrollWidth]")
        check(f"iPhone {label}: no horizontal overflow", scroll <= width, f"scrollWidth {scroll} > {width}")
        page.screenshot(path=str(SHOTS / f"iphone-{label}.png"), full_page=True)
    heights = page.eval_on_selector_all("nav[aria-label='Main mobile'] a", "links => links.map(l => l.getBoundingClientRect().height)")
    check("mobile tap targets at least 44 px tall", heights and min(heights) >= 44, heights)
    # Tapping works with touch emulation: navigate via the bottom bar.
    page.tap("nav[aria-label='Main mobile'] >> text=Documents")
    page.wait_for_url(BASE + "/documents")
    check("bottom navigation tap", page.url.endswith("/documents"))

    phone_browser.close()

print("M5 UI tests passed")
