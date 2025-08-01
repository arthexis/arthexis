from datetime import datetime
from pathlib import Path

from django.conf import settings
from selenium import webdriver
from selenium.webdriver.firefox.options import Options

SCREENSHOT_DIR = settings.LOG_DIR / "screenshots"


def capture_screenshot(url: str) -> Path:
    """Capture a screenshot of ``url`` and save it to :data:`SCREENSHOT_DIR`."""
    options = Options()
    options.add_argument("-headless")
    with webdriver.Firefox(options=options) as browser:
        browser.set_window_size(1280, 720)
        browser.get(url)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        filename = SCREENSHOT_DIR / f"{datetime.utcnow():%Y%m%d%H%M%S}.png"
        browser.save_screenshot(str(filename))
    return filename
