import pytest

from apps.selenium.models import SeleniumBrowser, SeleniumScript


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
    browser = SeleniumBrowser.objects.create(name="Default", is_default=True)
    script = SeleniumScript.objects.create(
        name="Inline",
        script="""
        https://example.com
        browser.get('https://next.test')
        """,
    )

    driver = DummyDriver()
    monkeypatch.setattr(SeleniumBrowser, "create_driver", lambda self: driver)

    script.execute()

    assert driver.visited == ["https://example.com", "https://next.test"]
    assert driver.quit_called is True


@pytest.mark.django_db
def test_callable_path_runs_after_start_url(monkeypatch):
    browser = SeleniumBrowser.objects.create(name="Default", is_default=True)
    script = SeleniumScript.objects.create(
        name="Callable",
        start_url="https://start.test",
        python_path="apps.selenium.tests.test_scripts.example_callable",
    )

    driver = DummyDriver()
    monkeypatch.setattr(SeleniumBrowser, "create_driver", lambda self: driver)

    script.execute()

    assert driver.visited == ["https://start.test", "https://callable.test"]
    assert driver.quit_called is True


def test_firefox_options_force_headless_without_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    browser = SeleniumBrowser(name="Example")

    options = browser._build_firefox_options()

    assert "-headless" in options.arguments
