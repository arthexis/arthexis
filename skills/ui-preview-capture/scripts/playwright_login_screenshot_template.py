"""Template Playwright script for deterministic Django admin login + screenshot.

Replace placeholders before execution:
- __PORT__
- __LOGIN_PATH__
- __USERNAME__
- __PASSWORD__
- __TARGET_PATH__
- __OUTPUT_PATH__
"""

from playwright.sync_api import sync_playwright


def run() -> None:
    """Run browser automation and capture a screenshot artifact."""
    base_url = "http://127.0.0.1:__PORT__"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"{base_url}__LOGIN_PATH__", wait_until="domcontentloaded")
        page.fill("input[name='username']", "__USERNAME__")
        page.fill("input[name='password']", "__PASSWORD__")
        page.click("input[type='submit'], button[type='submit']")
        page.goto(f"{base_url}__TARGET_PATH__", wait_until="networkidle")
        page.screenshot(path="__OUTPUT_PATH__", full_page=True)
        browser.close()


if __name__ == "__main__":
    run()
