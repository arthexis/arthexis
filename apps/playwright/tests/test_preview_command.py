from io import StringIO
from pathlib import Path
import sys
from types import ModuleType

import pytest
from django.core.management.base import CommandError

from apps.playwright.management.commands.preview import Command

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


def test_print_reports_emits_manifest_after_diagnostics(monkeypatch, tmp_path) -> None:
    """Preview reports should end with a concise manifest of generated artifacts."""

    command = Command()
    command.stdout = StringIO()
    output = tmp_path / "root-desktop.png"
    output.write_bytes(b"png")

    class _Report:
        width = 1440
        height = 900
        mean_brightness = 120.5
        white_pixel_ratio = 0.25

        def mostly_white(self) -> bool:
            return False

    monkeypatch.setattr(
        "apps.playwright.management.commands.preview.analyze_preview_image",
        lambda _: _Report(),
    )
    monkeypatch.setattr(command, "_display_path", lambda path: f"shown/{path.name}")

    command._print_reports(
        [
            {
                "path": "/",
                "viewport_name": "desktop",
                "viewport_size": (1440, 1800),
                "output": output,
            }
        ]
    )

    rendered = command.stdout.getvalue()
    assert "Saved preview to:" in rendered
    assert "Preview manifest:" in rendered
    assert "- / [desktop]: shown/root-desktop.png" in rendered


def test_write_preview_index_groups_paths_and_viewports(tmp_path) -> None:
    """Preview index should group captures by path with reviewer-friendly markdown links."""

    command = Command()
    output_dir = tmp_path / "preview_output"
    output_dir.mkdir()
    homepage = output_dir / "root-desktop.png"
    admin_mobile = output_dir / "admin-mobile.png"
    homepage.write_bytes(b"png")
    admin_mobile.write_bytes(b"png")

    command._write_preview_index(
        captures=[
            {
                "path": "/",
                "viewport_name": "desktop",
                "viewport_size": (1440, 1800),
                "output": homepage,
            },
            {
                "path": "/admin/",
                "viewport_name": "mobile",
                "viewport_size": (390, 844),
                "output": admin_mobile,
            },
        ],
        output_dir=output_dir,
    )

    index = (output_dir / "README.md").read_text()
    assert "# Preview Index" in index
    assert "## `/`" in index
    assert "## `/admin/`" in index
    assert "[root-desktop.png](root-desktop.png)" in index
    assert "[admin-mobile.png](admin-mobile.png)" in index


def test_build_capture_plan_uses_output_dir_when_custom_dir_is_provided(tmp_path) -> None:
    """Single-path desktop captures should stay within --output-dir when customized."""

    command = Command()
    output = tmp_path / "media" / "previews" / "admin-preview.png"
    output_dir = tmp_path / "preview_output"
    captures = command._build_capture_plan(
        paths=["/admin/"],
        viewport_names=["desktop"],
        output=output,
        output_dir=output_dir,
    )

    assert captures[0]["output"] == output_dir / "admin-desktop.png"


def test_write_preview_index_avoids_overwriting_existing_readme(tmp_path) -> None:
    """Preview index should not clobber pre-existing README files in output directories."""

    command = Command()
    command.stderr = StringIO()
    output_dir = tmp_path / "preview_output"
    output_dir.mkdir()
    readme = output_dir / "README.md"
    readme.write_text("# Existing docs\n", encoding="utf-8")
    homepage = output_dir / "root-desktop.png"
    homepage.write_bytes(b"png")

    command._write_preview_index(
        captures=[
            {
                "path": "/",
                "viewport_name": "desktop",
                "viewport_size": (1440, 1800),
                "output": homepage,
            }
        ],
        output_dir=output_dir,
    )

    assert readme.read_text(encoding="utf-8") == "# Existing docs\n"
    assert (output_dir / "PREVIEW_INDEX.md").is_file()
    assert "Refusing to overwrite existing README.md" in command.stderr.getvalue()


def _install_fake_selenium_modules(monkeypatch) -> None:
    """Install a minimal Selenium module tree so monkeypatch can target it."""

    class _FakeTimeoutException(Exception):
        """Placeholder Selenium timeout exception for unit tests."""

    class _FakeWebDriverException(Exception):
        """Placeholder Selenium driver exception for unit tests."""

    class _FakeBy:
        """Minimal Selenium ``By`` namespace used by preview tests."""

        CSS_SELECTOR = "css selector"
        ID = "id"

    modules = {
        name: ModuleType(name)
        for name in [
            "selenium",
            "selenium.common",
            "selenium.common.exceptions",
            "selenium.webdriver",
            "selenium.webdriver.common",
            "selenium.webdriver.common.by",
            "selenium.webdriver.chrome",
            "selenium.webdriver.chrome.options",
            "selenium.webdriver.support",
            "selenium.webdriver.support.ui",
        ]
    }

    modules["selenium"].common = modules["selenium.common"]
    modules["selenium"].webdriver = modules["selenium.webdriver"]
    modules["selenium.common"].exceptions = modules["selenium.common.exceptions"]
    modules["selenium.common.exceptions"].TimeoutException = _FakeTimeoutException
    modules["selenium.common.exceptions"].WebDriverException = _FakeWebDriverException
    modules["selenium.webdriver"].common = modules["selenium.webdriver.common"]
    modules["selenium.webdriver"].chrome = modules["selenium.webdriver.chrome"]
    modules["selenium.webdriver"].support = modules["selenium.webdriver.support"]
    modules["selenium.webdriver.common"].by = modules["selenium.webdriver.common.by"]
    modules["selenium.webdriver.common.by"].By = _FakeBy
    modules["selenium.webdriver.chrome"].options = modules["selenium.webdriver.chrome.options"]
    modules["selenium.webdriver.support"].ui = modules["selenium.webdriver.support.ui"]

    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)


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

    _install_fake_selenium_modules(monkeypatch)
    monkeypatch.setattr("selenium.webdriver.Chrome", lambda options: fake_driver, raising=False)
    monkeypatch.setattr("selenium.webdriver.chrome.options.Options", FakeChromeOptions, raising=False)
    monkeypatch.setattr("selenium.webdriver.support.ui.WebDriverWait", FakeWebDriverWait, raising=False)

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


def test_selenium_full_page_warns_and_uses_viewport_capture(monkeypatch, tmp_path) -> None:
    """Selenium should warn when full-page capture is requested but unsupported."""

    command = Command()
    command.stderr = StringIO()
    state: dict[str, object] = {
        "viewport_capture_calls": 0,
        "save_screenshot_calls": 0,
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

            return "complete"

        def get_screenshot_as_png(self) -> bytes:
            """Return PNG bytes for viewport-only captures."""

            state["viewport_capture_calls"] += 1
            return b"png"

        def quit(self) -> None:
            """Terminate the fake browser session."""

        def save_screenshot(self, path: str) -> None:
            """Record unsupported save_screenshot usage."""

            state["save_screenshot_calls"] += 1
            Path(path).write_bytes(b"png")

    class FakeWebDriverWait:
        """Immediate WebDriverWait replacement that records each wait."""

        def __init__(self, driver, timeout: int) -> None:
            self.driver = driver
            self.timeout = timeout

        def until(self, condition):
            """Run the wait predicate once and store the attempt."""

            return condition(self.driver)

    class FakeChromeOptions:
        """Minimal Chrome options stub."""

        def add_argument(self, argument: str) -> None:
            """Accept option arguments without side effects."""

    fake_driver = FakeDriver()

    _install_fake_selenium_modules(monkeypatch)
    monkeypatch.setattr("selenium.webdriver.Chrome", lambda options: fake_driver, raising=False)
    monkeypatch.setattr("selenium.webdriver.chrome.options.Options", FakeChromeOptions, raising=False)
    monkeypatch.setattr("selenium.webdriver.support.ui.WebDriverWait", FakeWebDriverWait, raising=False)

    output = tmp_path / "admin-preview.png"
    command._capture_all_selenium(
        base_url="http://127.0.0.1:8000",
        username="",
        password="",
        captures=[
            {
                "path": "/admin/",
                "viewport_size": (1440, 1800),
                "output": output,
            }
        ],
        browser_name="chrome",
        login_required=False,
        page_ready_state="load",
        ready_selectors=[],
        full_page=True,
    )

    assert state == {
        "viewport_capture_calls": 1,
        "save_screenshot_calls": 0,
    }
    assert output.read_bytes() == b"png"
    assert "treating --full-page as a viewport capture" in command.stderr.getvalue()
