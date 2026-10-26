#!/usr/bin/env python3
"""M5 slice 3: the real web UI in headless Firefox, desktop and iPhone sizes.

Usage: ui_test.py BASE_URL SETUP_CODE FIXTURE_PDF SCREENSHOT_DIR
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

BASE, SETUP_CODE, FIXTURE, SHOTS = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
PASSWORD = "admin-password-1"
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
    """The first time, the setup form makes the admin (signing in); afterwards a name and a password."""
    driver.get(BASE + "/")
    wait(driver, lambda d: d.find_elements(By.NAME, "setup-code") or d.find_elements(By.NAME, "password"))
    if driver.find_elements(By.NAME, "setup-code"):
        username = driver.find_element(By.NAME, "username")
        check("setup offers the admin that had only a token", username.get_attribute("value") == "admin")
        driver.save_screenshot(str(SHOTS / "setup.png"))
        driver.find_element(By.NAME, "setup-code").send_keys(SETUP_CODE.lower().replace("-", ""))
        check("setup code formatted as typed", driver.find_element(By.NAME, "setup-code").get_attribute("value") == SETUP_CODE)
        driver.find_element(By.NAME, "new-password").send_keys(PASSWORD)
        driver.find_element(By.NAME, "new-password-again").send_keys(PASSWORD)
    else:
        username = driver.find_element(By.NAME, "username")
        password = driver.find_element(By.NAME, "password")
        # A real login for password managers: a visible name, and the password as the current one.
        check("sign-in form is a plain name and password", username.is_displayed() and username.get_attribute("autocomplete") == "username"
              and password.get_attribute("type") == "password" and password.get_attribute("autocomplete") == "current-password")
        username.send_keys("admin")
        password.send_keys(PASSWORD)
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
    check("nothing kept in browser storage", driver.execute_script(
        "return localStorage.length + sessionStorage.length") == 0 and PASSWORD not in driver.page_source)
    # Admins see the people and can add one with a role.
    driver.get(BASE + "/system")
    wait(driver, lambda d: "admin" in d.find_element(By.CSS_SELECTOR, "[data-testid=people]").text)
    driver.find_element(By.XPATH, "//button[text()='Add a person']").click()
    form = driver.find_element(By.XPATH, "//button[text()='Add']/ancestor::form")
    form.find_element(By.CSS_SELECTOR, "input[autocomplete=off]").send_keys("reader")
    form.find_element(By.CSS_SELECTOR, "input[autocomplete=new-password]").send_keys("reader-password-1")
    form.find_element(By.CSS_SELECTOR, "select").send_keys("Read-only")
    form.find_element(By.XPATH, ".//button[text()='Add']").click()
    wait(driver, lambda d: "reader" in d.find_element(By.CSS_SELECTOR, "[data-testid=people]").text)
    check("an admin adds a person with a role", "reader" in driver.find_element(By.CSS_SELECTOR, "[data-testid=people]").text)
    driver.save_screenshot(str(SHOTS / "desktop-system-accounts.png"))
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
    images = listing.find_elements(By.TAG_NAME, "img")
    check("XSS payload rendered as text, no element created",
          all((image.get_attribute("data-testid") or "").startswith("preview-") and "/preview?" in image.get_attribute("src") for image in images)
          and not driver.execute_script("return !!window.__xss"), [image.get_attribute("outerHTML") for image in images])
    check("one list with ready and held files", "Ready" in listing.text and "Held" in listing.text)
    check("archived files show their title and folder", "Internet Invoice - October 2026" in listing.text
          and "Finance › Example Telecom" in listing.text, listing.text)
    driver.save_screenshot(str(SHOTS / "desktop-documents.png"))

    # The logo and the name lead to the main page.
    driver.find_element(By.CSS_SELECTOR, "[data-testid=home-link]").click()
    wait(driver, lambda d: d.current_url.rstrip("/") == BASE.rstrip("/"))
    check("logo and name go to the main page", "Add PDF files" in body(driver))

    # Sidebar: the archive's sections and correspondents, with counts; picking one filters the list.
    tree = wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=browse-tree]")))
    wait(driver, lambda d: "Finance" in d.find_element(By.CSS_SELECTOR, "[data-testid=browse-tree]").text)
    check("sidebar lists the sections with counts", "Finance" in tree.text and "Everything" in tree.text, tree.text)
    finance = tree.find_element(By.CSS_SELECTOR, "[data-section=Finance]")
    finance.find_element(By.CSS_SELECTOR, "button[aria-expanded]").click()
    finance.find_element(By.PARTIAL_LINK_TEXT, "Example Telecom").click()
    wait(driver, lambda d: "from=Example" in d.current_url)
    heading = wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=documents-heading]")))
    rows = driver.find_element(By.CSS_SELECTOR, "[data-testid=document-list]").text
    check("a correspondent shows only its documents", "Example Telecom" in heading.text and "Internet Invoice" in rows and HOSTILE not in rows, rows)

    # Thumbnails: the gateway's pictures of the clean copies (rendered by the archive worker).
    wait(driver, lambda d: d.execute_script(
        "return [...document.querySelectorAll('[data-testid=document-list] img')].some(i => i.complete && i.naturalWidth > 0)"), 30)
    check("list shows first-page thumbnails", True)
    driver.find_element(By.CSS_SELECTOR, "[data-testid=view-grid]").click()
    check("grid view", driver.find_elements(By.CSS_SELECTOR, "[data-testid=document-grid] img"))
    driver.find_element(By.CSS_SELECTOR, "[data-testid=view-list]").click()

    # Search reads the documents' own text too (case, accents and spacing aside).
    driver.get(BASE + "/documents")
    wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=search]"))).send_keys("TOTAL due")
    snippet = wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=snippet]")), 20)
    check("text search finds a phrase in the document", "29.99" in snippet.text or "Total" in snippet.text, snippet.text)

    # Needs attention: the held file, counted in the navigation.
    count = driver.find_element(By.CSS_SELECTOR, "[data-testid=attention-count]")
    check("navigation counts what needs attention", int(count.text) >= 1, count.text)
    driver.find_element(By.CSS_SELECTOR, "[data-testid=nav-attention]").click()
    held = wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=attention-held]")))
    check("attention lists the held file", HOSTILE in held.text, held.text)

    # Add files: choosing a file starts it at once (archive and Paperless, no options to pick).
    driver.get(BASE + "/")
    wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=file-input]"))).send_keys(FIXTURE)
    link = wait(driver, ec.element_to_be_clickable((By.LINK_TEXT, "Open")))
    link.click()
    text_present(driver, "invoice-en.pdf")
    # No workers run in this stack, so the new upload waits (live status shown in words).
    text_present(driver, "Checking your file")
    check("upload accepted and tracked on its document page", "/documents/scan-" in driver.current_url)
    progress = wait(driver, ec.presence_of_element_located((By.CSS_SELECTOR, "[data-testid=progress]")))
    check("progress shows five stages, at the checks", progress.get_attribute("data-stage") == "check"
          and len(progress.find_elements(By.CSS_SELECTOR, "li[data-stage]")) == 5, progress.get_attribute("outerHTML")[:300])
    line = driver.find_element(By.CSS_SELECTOR, "[data-testid=progress-line]").text
    check("progress says what is happening and how long is left", "Checking" in line and "left" in line, line)
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
    # Click a value to change it; Undo puts it back.
    driver.find_element(By.CSS_SELECTOR, "[data-testid=inline-reference]").click()
    box = driver.find_element(By.CSS_SELECTOR, "[data-testid=inline-form-reference] input")
    box.clear()
    box.send_keys("INV-INLINE-7")
    driver.find_element(By.CSS_SELECTOR, "[data-testid=inline-form-reference] button[type=submit]").click()
    wait(driver, lambda d: "INV-INLINE-7" in d.find_element(By.CSS_SELECTOR, "[data-testid=copy-fields]").text, 30)
    check("inline edit saves", driver.find_elements(By.CSS_SELECTOR, "[data-testid=saved-note]"))
    driver.find_element(By.CSS_SELECTOR, "[data-testid=undo]").click()
    wait(driver, lambda d: "2026-10-4471" in d.find_element(By.CSS_SELECTOR, "[data-testid=copy-fields]").text, 30)
    check("undo restores the previous value", "INV-INLINE-7" not in driver.find_element(By.CSS_SELECTOR, "[data-testid=copy-fields]").text)
    preview = driver.find_element(By.CSS_SELECTOR, "[data-testid=large-preview] img")
    wait(driver, lambda d: d.execute_script("return arguments[0].complete && arguments[0].naturalWidth > 0", preview), 30)
    check("document page shows a large first-page picture", "size=large" in preview.get_attribute("src"))

    # Files dropped on any page are added (and shown on the main page).
    driver.execute_script("""
        const file = new File(['%PDF-1.4\\n%%EOF\\n'], 'dropped.pdf', {type: 'application/pdf'});
        const data = new DataTransfer(); data.items.add(file);
        document.body.dispatchEvent(new DragEvent('dragenter', {bubbles: true, dataTransfer: data}));""")
    check("dragging files over a page shows the drop area", driver.find_elements(By.CSS_SELECTOR, "[data-testid=drop-overlay]"))
    driver.execute_script("""
        const file = new File(['%PDF-1.4\\n%%EOF\\n'], 'dropped.pdf', {type: 'application/pdf'});
        const data = new DataTransfer(); data.items.add(file);
        document.body.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: data}));""")
    wait(driver, lambda d: d.current_url.rstrip("/") == BASE.rstrip("/"))
    text_present(driver, "dropped.pdf")
    check("a file dropped anywhere is added", True)
    driver.get(BASE + "/documents/scan-01J00000000000000000000060")
    wait(driver, lambda d: d.find_elements(By.CSS_SELECTOR, "[data-testid=view-pdf]"))
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
    wait(driver, ec.presence_of_element_located((By.NAME, "password")))
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
    driver.get(BASE + "/")
    wait(driver, ec.presence_of_element_located((By.NAME, "password")))
    driver.save_screenshot(str(SHOTS / "desktop-sign-in-dark.png"))
    login(driver)
    driver.get(BASE + "/documents/scan-01J00000000000000000000060")
    wait(driver, lambda d: d.find_elements(By.CSS_SELECTOR, "[data-testid=download-pdf]"))
    background = driver.execute_script("return getComputedStyle(document.body).backgroundColor")
    check("dark system gives a dark page", driver.execute_script("return document.documentElement.classList.contains('dark')")
          and background != "oklch(0.984 0.003 247.858)", background)
    schemes = driver.execute_script("""const frame = document.createElement('iframe'); document.body.appendChild(frame);
        const value = [getComputedStyle(document.documentElement).colorScheme, getComputedStyle(document.body).colorScheme,
                       getComputedStyle(frame).colorScheme]; frame.remove(); return value;""")
    check("dark from the body down; the page and frames stay normal (no white Bitwarden frame)",
          schemes == ["normal", "dark", "normal"], schemes)
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
    page.fill("input[name=username]", "admin")
    page.fill("input[name=password]", PASSWORD)
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
    check("phone: no sidebar", not page.is_visible("aside[aria-label=Sidebar]"))
    page.tap("[data-testid=browse]")
    page.wait_for_selector("[data-testid=browse-sheet] [data-testid=browse-tree]")
    check("phone: Browse opens the sections", "Finance" in page.inner_text("[data-testid=browse-sheet]"))
    page.tap("[data-testid=browse-sheet] >> text=Finance")
    page.wait_for_url("**/documents?section=Finance")
    check("phone: picking a section filters and closes", not page.is_visible("[data-testid=browse-sheet]"))

    phone_browser.close()

print("M5 UI tests passed")
