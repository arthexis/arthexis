import sys

import pytest

from apps.playwright.models import PlaywrightBrowser, PlaywrightScript


class DummyDriver:
    def __init__(self):
        self.visited = []
        self.quit_called = False

    def get(self, url):  # pragma: no cover - simple test double
        self.visited.append(url)

    def quit(self):  # pragma: no cover - simple test double
        self.quit_called = True


def example_callable(browser, script=None):  # pragma: no cover - used via import path
    browser.get("https://callable.test")


@pytest.mark.django_db
def test_inline_script_executes_after_start_url(monkeypatch):
    PlaywrightBrowser.objects.create(name="Default", is_default=True)
    script = PlaywrightScript.objects.create(
        name="Inline",
        script="""
        https://example.com
        browser.get('https://next.test')
        """,
    )

    driver = DummyDriver()
    monkeypatch.setattr(PlaywrightBrowser, "create_driver", lambda self: driver)

    script.execute()

    assert driver.visited == ["https://example.com", "https://next.test"]
    assert driver.quit_called is True


@pytest.mark.django_db
def test_callable_path_runs_after_start_url(monkeypatch):
    PlaywrightBrowser.objects.create(name="Default", is_default=True)
    script = PlaywrightScript.objects.create(
        name="Callable",
        start_url="https://start.test",
        python_path="apps.playwright.tests.test_scripts.example_callable",
    )

    driver = DummyDriver()
    monkeypatch.setattr(PlaywrightBrowser, "create_driver", lambda self: driver)

    script.execute()

    assert driver.visited == ["https://start.test", "https://callable.test"]
    assert driver.quit_called is True


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Display-environment detection is Linux-specific in this suite.",
)
def test_browser_forces_headless_without_display(monkeypatch):
    """Headed mode should downgrade to headless when DISPLAY is unavailable."""

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    browser = PlaywrightBrowser(name="Example", mode=PlaywrightBrowser.Mode.HEADED)

    assert browser._headless_mode() is True
