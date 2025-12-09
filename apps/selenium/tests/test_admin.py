from django.contrib import admin

from apps.selenium.admin import SeleniumBrowserAdmin
from apps.selenium.models import SeleniumBrowser


def make_admin(browser_cls=SeleniumBrowser):
    return SeleniumBrowserAdmin(browser_cls, admin.site)


def make_browser(**kwargs):
    defaults = {
        "name": "Test Browser",
        "mode": SeleniumBrowser.Mode.HEADED,
        "engine": SeleniumBrowser.Engine.FIREFOX,
    }
    defaults.update(kwargs)
    return SeleniumBrowser(**defaults)


class DummyDriver:
    def __init__(self):
        self.closed = False

    def quit(self):
        self.closed = True


def test_test_browsers_success_with_advice(monkeypatch):
    admin_instance = make_admin()
    messages = []
    admin_instance.message_user = lambda request, msg, level=None: messages.append(
        (msg, level)
    )

    dummy_driver = DummyDriver()
    monkeypatch.setattr(SeleniumBrowser, "create_driver", lambda self: dummy_driver)
    monkeypatch.delenv("DISPLAY", raising=False)

    browser = make_browser()

    admin_instance.test_browsers(None, [browser])

    assert any("started successfully" in msg for msg, _ in messages)
    assert any("headless mode" in msg for msg, _ in messages)
    assert dummy_driver.closed is True


def test_test_browsers_reports_errors(monkeypatch):
    admin_instance = make_admin()
    messages = []
    admin_instance.message_user = lambda request, msg, level=None: messages.append(
        (msg, level)
    )

    def failing_driver(_self):
        raise RuntimeError("boom")

    monkeypatch.setattr(SeleniumBrowser, "create_driver", failing_driver)
    monkeypatch.delenv("DISPLAY", raising=False)

    browser = make_browser(binary_path="/missing/firefox")

    admin_instance.test_browsers(None, [browser])

    assert any("Failed to start" in msg for msg, _ in messages)
    assert any("binary path" in msg for msg, _ in messages)
