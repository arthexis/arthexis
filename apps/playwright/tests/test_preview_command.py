from io import StringIO
from pathlib import Path

import pytest
from django.core.management.base import CommandError

from apps.playwright.management.commands.preview import Command


def test_handle_reports_backend_failures_without_name_error(monkeypatch) -> None:
    """Backend failure aggregation should raise a clean CommandError message."""

    command = Command()

    deleted_ids: list[int | None] = []

    monkeypatch.setattr(
        command, "_create_throwaway_admin_user", lambda: ("tmp", "pw", 42)
    )
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", deleted_ids.append)
    monkeypatch.setattr(command, "_build_capture_plan", lambda **kwargs: [])

    def _always_fail(**kwargs):
        raise CommandError("boom")

    monkeypatch.setattr(command, "_capture_with_backend", _always_fail)

    with pytest.raises(
        CommandError, match=r"All preview backends failed\. Last error: boom"
    ):
        command.handle(
            base_url="http://127.0.0.1:8000",
            paths=["/admin/"],
            username=None,
            password=None,
            output="media/previews/admin-preview.png",
            output_dir="",
            viewports="desktop",
            backend="playwright,selenium",
            engine="chromium,firefox",
            no_login=False,
        )

    assert deleted_ids == [42]


def test_handle_uses_throwaway_user_and_cleans_it_up(monkeypatch) -> None:
    """Preview captures should use a temporary login user and remove it afterwards."""

    command = Command()
    state: dict[str, object] = {
        "created": False,
        "deleted": None,
        "login_required": None,
    }

    def _create_user() -> tuple[str, str, int]:
        state["created"] = True
        return "preview-user", "preview-pass", 99

    def _delete_user(user_id: int | None) -> None:
        state["deleted"] = user_id

    def _capture_with_backend(**kwargs):
        state["login_required"] = kwargs["login_required"]

    monkeypatch.setattr(command, "_create_throwaway_admin_user", _create_user)
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", _delete_user)
    monkeypatch.setattr(command, "_build_capture_plan", lambda **kwargs: [])
    monkeypatch.setattr(command, "_capture_with_backend", _capture_with_backend)
    monkeypatch.setattr(command, "_print_reports", lambda captures: None)

    command.handle(
        base_url="http://127.0.0.1:8000",
        paths=["/admin/"],
        username=None,
        password=None,
        output="media/previews/admin-preview.png",
        output_dir="",
        viewports="desktop",
        backend="playwright",
        engine="chromium",
        no_login=False,
    )

    assert state["created"] is True
    assert state["login_required"] is True
    assert state["deleted"] == 99


def test_handle_cleans_up_throwaway_user_on_validation_failure(monkeypatch) -> None:
    """Throwaway preview user should be deleted when argument validation fails."""

    command = Command()
    state: dict[str, object] = {"deleted": None}

    monkeypatch.setattr(
        command,
        "_create_throwaway_admin_user",
        lambda: ("preview-user", "preview-pass", 99),
    )
    monkeypatch.setattr(
        command,
        "_delete_throwaway_admin_user",
        lambda user_id: state.__setitem__("deleted", user_id),
    )

    with pytest.raises(CommandError, match="At least one viewport profile"):
        command.handle(
            base_url="http://127.0.0.1:8000",
            paths=["/admin/"],
            username=None,
            password=None,
            output="media/previews/admin-preview.png",
            output_dir="",
            viewports=",",
            backend="playwright",
            engine="chromium",
            no_login=False,
        )

    assert state["deleted"] == 99


def test_handle_skips_login_and_user_creation_for_no_login(monkeypatch) -> None:
    """No-login captures should not create or authenticate any temporary user."""

    command = Command()
    state: dict[str, object] = {
        "created": False,
        "deleted": None,
        "login_required": None,
    }

    def _create_user() -> tuple[str, str, int]:
        state["created"] = True
        return "preview-user", "preview-pass", 99

    def _delete_user(user_id: int | None) -> None:
        state["deleted"] = user_id

    def _capture_with_backend(**kwargs):
        state["login_required"] = kwargs["login_required"]

    monkeypatch.setattr(command, "_create_throwaway_admin_user", _create_user)
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", _delete_user)
    monkeypatch.setattr(command, "_build_capture_plan", lambda **kwargs: [])
    monkeypatch.setattr(command, "_capture_with_backend", _capture_with_backend)
    monkeypatch.setattr(command, "_print_reports", lambda captures: None)

    command.handle(
        base_url="http://127.0.0.1:8000",
        paths=["/admin/"],
        username=None,
        password=None,
        output="media/previews/admin-preview.png",
        output_dir="",
        viewports="desktop",
        backend="playwright",
        engine="chromium",
        no_login=True,
    )

    assert state["created"] is False
    assert state["login_required"] is False
    assert state["deleted"] is None


def test_handle_falls_back_to_selenium_backend(monkeypatch) -> None:
    """Preview should try Selenium automatically when Playwright backend fails."""

    command = Command()
    attempted_backends: list[str] = []

    monkeypatch.setattr(
        command, "_create_throwaway_admin_user", lambda: ("tmp", "pw", 42)
    )
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", lambda _: None)
    monkeypatch.setattr(command, "_build_capture_plan", lambda **kwargs: [])
    monkeypatch.setattr(command, "_print_reports", lambda captures: None)

    def _capture_with_backend(**kwargs):
        attempted_backends.append(kwargs["backend"])
        if kwargs["backend"] == "playwright":
            raise CommandError("playwright unavailable")

    monkeypatch.setattr(command, "_capture_with_backend", _capture_with_backend)

    command.handle(
        base_url="http://127.0.0.1:8000",
        paths=["/admin/"],
        username=None,
        password=None,
        output="media/previews/admin-preview.png",
        output_dir="",
        viewports="desktop",
        backend="playwright,selenium",
        engine="chromium",
        no_login=False,
    )

    assert attempted_backends == ["playwright", "selenium"]


def test_handle_reports_missing_screenshot_artifacts(monkeypatch, tmp_path) -> None:
    """Preview should fail clearly when a backend returns without saving files."""

    command = Command()

    monkeypatch.setattr(
        command, "_create_throwaway_admin_user", lambda: ("tmp", "pw", 42)
    )
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", lambda _: None)
    monkeypatch.setattr(
        command,
        "_build_capture_plan",
        lambda **kwargs: [
            {
                "path": "/admin/",
                "viewport_name": "desktop",
                "viewport_size": (1440, 1800),
                "output": tmp_path / "missing-admin-preview.png",
            }
        ],
    )
    monkeypatch.setattr(command, "_capture_all_playwright", lambda **kwargs: None)
    monkeypatch.setattr(command, "_print_reports", lambda captures: None)

    with pytest.raises(
        CommandError, match=r"did not produce the expected screenshot artifact"
    ):
        command.handle(
            base_url="http://127.0.0.1:8000",
            paths=["/admin/"],
            username=None,
            password=None,
            output="media/previews/admin-preview.png",
            output_dir="",
            viewports="desktop",
            backend="playwright",
            engine="chromium",
            no_login=False,
        )


def test_handle_falls_back_to_secondary_engine_when_first_misses_artifact(
    monkeypatch, tmp_path
) -> None:
    """Preview should keep trying engines when an earlier one skips an artifact."""

    command = Command()
    attempted_engines: list[str] = []
    output = tmp_path / "admin-preview.png"

    monkeypatch.setattr(
        command, "_create_throwaway_admin_user", lambda: ("tmp", "pw", 42)
    )
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", lambda _: None)
    monkeypatch.setattr(
        command,
        "_build_capture_plan",
        lambda **kwargs: [
            {
                "path": "/admin/",
                "viewport_name": "desktop",
                "viewport_size": (1440, 1800),
                "output": output,
            }
        ],
    )
    monkeypatch.setattr(command, "_print_reports", lambda captures: None)

    def _capture_all_playwright(**kwargs):
        attempted_engines.append(kwargs["engine"])
        if kwargs["engine"] == "firefox":
            kwargs["captures"][0]["output"].write_text("firefox")

    monkeypatch.setattr(command, "_capture_all_playwright", _capture_all_playwright)

    command.handle(
        base_url="http://127.0.0.1:8000",
        paths=["/admin/"],
        username=None,
        password=None,
        output="media/previews/admin-preview.png",
        output_dir="",
        viewports="desktop",
        backend="playwright",
        engine="chromium,firefox",
        no_login=False,
    )

    assert attempted_engines == ["chromium", "firefox"]
    assert output.read_text() == "firefox"


def test_handle_clears_stale_artifacts_between_engine_retries(monkeypatch, tmp_path) -> None:
    """Preview should not treat mixed outputs from multiple failed engines as success."""

    command = Command()
    outputs = [tmp_path / "admin-desktop.png", tmp_path / "admin-mobile.png"]

    monkeypatch.setattr(
        command, "_create_throwaway_admin_user", lambda: ("tmp", "pw", 42)
    )
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", lambda _: None)
    monkeypatch.setattr(
        command,
        "_build_capture_plan",
        lambda **kwargs: [
            {
                "path": "/admin/",
                "viewport_name": "desktop",
                "viewport_size": (1440, 1800),
                "output": outputs[0],
            },
            {
                "path": "/admin/",
                "viewport_name": "mobile",
                "viewport_size": (390, 844),
                "output": outputs[1],
            },
        ],
    )
    monkeypatch.setattr(command, "_print_reports", lambda captures: None)

    def _capture_all_playwright(**kwargs):
        engine = kwargs["engine"]
        if engine == "chromium":
            outputs[0].write_text("chromium")
            return
        if engine == "firefox":
            outputs[1].write_text("firefox")
            return
        raise AssertionError(f"Unexpected engine: {engine}")

    monkeypatch.setattr(command, "_capture_all_playwright", _capture_all_playwright)

    with pytest.raises(
        CommandError,
        match=r"All preview backends failed\. Last error: All Playwright engines failed",
    ):
        command.handle(
            base_url="http://127.0.0.1:8000",
            paths=["/admin/"],
            username=None,
            password=None,
            output="media/previews/admin-preview.png",
            output_dir="",
            viewports="desktop,mobile",
            backend="playwright",
            engine="chromium,firefox",
            no_login=False,
        )

    assert outputs[0].exists() is False
    assert outputs[1].exists() is True
    assert outputs[1].read_text() == "firefox"


def test_handle_waits_for_suite_when_requested(monkeypatch) -> None:
    """Preview should probe suite readiness before capturing when requested."""

    command = Command()
    state: dict[str, object] = {"wait_called": False}

    monkeypatch.setattr(
        command,
        "_create_throwaway_admin_user",
        lambda: ("preview-user", "preview-pass", 99),
    )
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", lambda _: None)
    monkeypatch.setattr(command, "_build_capture_plan", lambda **kwargs: [])
    monkeypatch.setattr(command, "_capture_with_backend", lambda **kwargs: None)
    monkeypatch.setattr(command, "_print_reports", lambda captures: None)

    def _wait_for_suite_ready(**kwargs):
        state["wait_called"] = kwargs == {
            "base_url": "http://127.0.0.1:8000",
            "timeout_seconds": 10,
        }

    monkeypatch.setattr(command, "_wait_for_suite_ready", _wait_for_suite_ready)

    command.handle(
        base_url="http://127.0.0.1:8000",
        paths=["/admin/"],
        username=None,
        password=None,
        output="media/previews/admin-preview.png",
        output_dir="",
        viewports="desktop",
        backend="playwright",
        engine="chromium",
        no_login=False,
        wait_for_suite=True,
        suite_timeout=10,
    )

    assert state["wait_called"] is True


def test_wait_for_suite_ready_rejects_non_positive_timeout() -> None:
    """Suite wait should fail fast when timeout is non-positive."""

    command = Command()

    with pytest.raises(CommandError, match="--suite-timeout must be greater than zero"):
        command._wait_for_suite_ready(
            base_url="http://127.0.0.1:8000", timeout_seconds=0
        )


def test_normalize_options_applies_ci_fast_defaults() -> None:
    """CI-fast preset should collapse preview captures to the fast single-browser path."""

    command = Command()

    normalized = command._normalize_options(
        {
            "base_url": "http://127.0.0.1:8000",
            "paths": ["/admin/"],
            "output": "media/previews/admin-preview.png",
            "output_dir": "",
            "viewports": "desktop,mobile",
            "backend": "playwright,selenium",
            "engine": "chromium,firefox",
            "no_login": False,
            "wait_for_suite": False,
            "suite_timeout": 60,
            "page_ready_state": "networkidle",
            "ready_selectors": [],
            "full_page": True,
            "ci_fast": True,
        }
    )

    assert normalized["backend"] == "playwright"
    assert normalized["engine"] == "chromium"
    assert normalized["viewports"] == "desktop"
    assert normalized["page_ready_state"] == "domcontentloaded"
    assert normalized["full_page"] is False


def test_handle_passes_effective_capture_options_to_backend(monkeypatch) -> None:
    """Preview should forward normalized readiness and screenshot options to the backend."""

    command = Command()
    state: dict[str, object] = {}

    monkeypatch.setattr(
        command,
        "_create_throwaway_admin_user",
        lambda: ("preview-user", "preview-pass", 99),
    )
    monkeypatch.setattr(command, "_delete_throwaway_admin_user", lambda _: None)
    monkeypatch.setattr(command, "_build_capture_plan", lambda **kwargs: [])
    monkeypatch.setattr(command, "_print_reports", lambda captures: None)

    def _capture_with_backend(**kwargs):
        state.update(
            {
                "backend": kwargs["backend"],
                "engines": kwargs["engines"],
                "page_ready_state": kwargs["page_ready_state"],
                "ready_selectors": kwargs["ready_selectors"],
                "full_page": kwargs["full_page"],
            }
        )

    monkeypatch.setattr(command, "_capture_with_backend", _capture_with_backend)

    command.handle(
        base_url="http://127.0.0.1:8000",
        paths=["/admin/"],
        username=None,
        password=None,
        output="media/previews/admin-preview.png",
        output_dir="",
        viewports="desktop,mobile",
        backend="playwright,selenium",
        engine="chromium,firefox",
        no_login=False,
        wait_for_suite=False,
        suite_timeout=60,
        page_ready_state="load",
        ready_selectors=["#content", ".dashboard"],
        full_page=False,
        ci_fast=False,
    )

    assert state == {
        "backend": "playwright",
        "engines": ["chromium", "firefox"],
        "page_ready_state": "load",
        "ready_selectors": ["#content", ".dashboard"],
        "full_page": False,
    }


def test_selenium_networkidle_warns_and_uses_load_wait(monkeypatch, tmp_path) -> None:
    """Selenium should warn that networkidle falls back to the load-ready check."""

    command = Command()
    command.stderr = StringIO()
    state: dict[str, object] = {
        "wait_checks": 0,
        "scripts": [],
    }

    class FakeDriver:
        """Minimal Selenium driver stub for screenshot capture tests."""

        current_url = "http://127.0.0.1:8000/admin/"

        def get(self, url: str) -> None:
            """Record the current navigation target."""

            self.current_url = url

        def set_window_size(self, width: int, height: int) -> None:
            """Accept the requested viewport size."""

        def execute_script(self, script: str) -> str:
            """Return a completed ready state for wait assertions."""

            state["scripts"].append(script)
            return "complete"

        def get_screenshot_as_png(self) -> bytes:
            """Return PNG bytes for viewport-only captures."""

            return b"png"

        def quit(self) -> None:
            """Terminate the fake browser session."""

        def save_screenshot(self, path: str) -> None:
            """Write a screenshot file for full-page captures."""

            Path(path).write_bytes(b"png")

    class FakeWebDriverWait:
        """Immediate WebDriverWait replacement that records each wait."""

        def __init__(self, driver, timeout: int) -> None:
            self.driver = driver
            self.timeout = timeout

        def until(self, condition):
            """Run the wait predicate once and store the attempt."""

            state["wait_checks"] += 1
            return condition(self.driver)

    class FakeChromeOptions:
        """Minimal Chrome options stub."""

        def add_argument(self, argument: str) -> None:
            """Accept option arguments without side effects."""

    fake_driver = FakeDriver()

    monkeypatch.setattr("selenium.webdriver.Chrome", lambda options: fake_driver)
    monkeypatch.setattr("selenium.webdriver.chrome.options.Options", FakeChromeOptions)
    monkeypatch.setattr("selenium.webdriver.support.ui.WebDriverWait", FakeWebDriverWait)

    command._capture_all_selenium(
        base_url="http://127.0.0.1:8000",
        username="",
        password="",
        captures=[
            {
                "path": "/admin/",
                "viewport_size": (1440, 1800),
                "output": tmp_path / "admin-preview.png",
            }
        ],
        browser_name="chrome",
        login_required=False,
        page_ready_state="networkidle",
        ready_selectors=[],
        full_page=False,
    )

    assert state["wait_checks"] == 1
    assert state["scripts"] == ["return document.readyState"]
    assert "treating --page-ready-state=networkidle as load" in command.stderr.getvalue()
