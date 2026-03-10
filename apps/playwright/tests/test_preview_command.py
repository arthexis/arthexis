import pytest
from django.core.management import call_command

from apps.playwright.management.commands.preview import Command


def test_preview_command_help_lists_expected_options(capsys):
    """The short preview command should expose the expected CLI contract."""

    with pytest.raises(SystemExit):
        call_command("preview", "--help")

    output = capsys.readouterr().out
    assert "--base-url" in output
    assert "--engine" in output


class _FakePage:
    """Minimal page stub for preview command login-flow tests."""

    def __init__(self, *, url: str) -> None:
        self.url = url
        self.fills: list[tuple[str, str]] = []
        self.clicks: list[str] = []
        self.gotos: list[tuple[str, str]] = []

    def wait_for_selector(self, _selector: str, timeout: int) -> None:  # noqa: ARG002
        return None

    def fill(self, selector: str, value: str) -> None:
        self.fills.append((selector, value))

    def click(self, selector: str) -> None:
        self.clicks.append(selector)

    def goto(self, url: str, wait_until: str) -> None:
        self.gotos.append((url, wait_until))


def test_complete_login_if_needed_skips_admin_login_page() -> None:
    """Fallback login should be skipped when already on admin login."""

    page = _FakePage(url="http://127.0.0.1:8000/admin/login/")
    command = Command()

    command._complete_login_if_needed(
        page=page,
        username="admin",
        password="admin123",
        capture_url="http://127.0.0.1:8000/ocpp/evcs/simulator/",
        timeout_error=RuntimeError,
    )

    assert page.fills == []
    assert page.clicks == []
    assert page.gotos == []


def test_complete_login_if_needed_authenticates_non_admin_login_page() -> None:
    """Fallback login should submit credentials when capture flow lands on site login."""

    page = _FakePage(url="http://127.0.0.1:8000/login/?next=/ocpp/evcs/simulator/")
    command = Command()

    command._complete_login_if_needed(
        page=page,
        username="admin",
        password="admin123",
        capture_url="http://127.0.0.1:8000/ocpp/evcs/simulator/",
        timeout_error=RuntimeError,
    )

    assert page.fills == [("#id_username", "admin"), ("#id_password", "admin123")]
    assert page.clicks == ["button[type='submit'], input[type='submit']"]
    assert page.gotos == [("http://127.0.0.1:8000/ocpp/evcs/simulator/", "domcontentloaded")]
