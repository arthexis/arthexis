"""Template Playwright script for deterministic Django admin login + screenshot.

Replace placeholders before execution:
- __PORT__
- __LOGIN_PATH__
- __USERNAME__
- __PASSWORD__
- __TARGET_PATH__
- __OUTPUT_PATH__
"""

from urllib.parse import urljoin

from playwright.sync_api import sync_playwright


def _safe_url(base_url: str, path: str) -> str:
    """Build a same-origin URL safely from a base URL and path placeholder."""
    normalized_path = f"/{path.lstrip('/')}"
    return urljoin(f"{base_url}/", normalized_path)


def run() -> None:
    """Run browser automation and capture a screenshot artifact."""
    base_url = "http://127.0.0.1:__PORT__"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            browser = p.firefox.launch()

        page = browser.new_page()
        login_url = _safe_url(base_url, "__LOGIN_PATH__")
        target_url = _safe_url(base_url, "__TARGET_PATH__")

        page.goto(login_url, wait_until="domcontentloaded")
        page.fill("input[name='username']", "__USERNAME__")
        page.fill("input[name='password']", "__PASSWORD__")
        page.click("input[type='submit'], button[type='submit']")
        page.wait_for_url(lambda url: url.rstrip("/") != login_url.rstrip("/"), timeout=10000)
        if page.url.rstrip("/") == login_url.rstrip("/"):
            browser.close()
            raise RuntimeError("Login appears to have failed: still on login page after submit.")

        page.goto(target_url, wait_until="networkidle")
        page.screenshot(path="__OUTPUT_PATH__", full_page=True)
        browser.close()


if __name__ == "__main__":
    run()
