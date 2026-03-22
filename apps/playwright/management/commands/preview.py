"""Capture deterministic admin previews and emit lightweight image diagnostics."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, IntegrityError

from apps.playwright.preview_tool import analyze_preview_image

DEFAULT_VIEWPORTS: dict[str, tuple[int, int]] = {
    "desktop": (1440, 1800),
    "tablet": (1024, 1366),
    "mobile": (390, 844),
}
ENGINE_TO_SELENIUM_BROWSER = {
    "chromium": "chrome",
    "firefox": "firefox",
}
SUPPORTED_BACKENDS = ("playwright", "selenium")


class Command(BaseCommand):
    """Login to Django admin and capture deterministic screenshots."""

    help = "Capture admin preview screenshots and print simple image health summaries."

    def add_arguments(self, parser):
        """Register CLI arguments for the preview command."""

        parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Server base URL.")
        parser.add_argument(
            "--path",
            dest="paths",
            action="append",
            default=[],
            help="Path to capture after login. Repeat for multiple pages.",
        )
        parser.add_argument("--username", default=None, help="Legacy option; ignored.")
        parser.add_argument("--password", default=None, help="Legacy option; ignored.")
        parser.add_argument(
            "--output",
            default="media/previews/admin-preview.png",
            help="Legacy output file for the desktop capture when a single path is used.",
        )
        parser.add_argument(
            "--output-dir",
            default="",
            help="Directory for generated captures. Defaults to the output file directory.",
        )
        parser.add_argument(
            "--viewports",
            default=",".join(DEFAULT_VIEWPORTS),
            help="Comma-separated viewport profiles to capture (desktop,tablet,mobile).",
        )
        parser.add_argument(
            "--backend",
            default=",".join(SUPPORTED_BACKENDS),
            help="Comma-separated capture backends in fallback order (playwright,selenium).",
        )
        parser.add_argument(
            "--engine",
            default="chromium,firefox",
            help="Comma-separated browser engine fallback order (chromium,firefox,webkit).",
        )
        parser.add_argument(
            "--no-login",
            action="store_true",
            help="Capture pages without authenticating first.",
        )
        parser.add_argument(
            "--wait-for-suite",
            action="store_true",
            help="Wait for the suite base URL to become reachable before capture starts.",
        )
        parser.add_argument(
            "--suite-timeout",
            default=60,
            type=int,
            help="Maximum seconds to wait when --wait-for-suite is enabled.",
        )
        parser.add_argument(
            "--page-ready-state",
            default="networkidle",
            choices=("domcontentloaded", "load", "networkidle"),
            help="Browser load state to wait for before capturing each page.",
        )
        parser.add_argument(
            "--ready-selector",
            dest="ready_selectors",
            action="append",
            default=[],
            help="CSS selector to wait for after each navigation. Repeat for multiple selectors.",
        )
        parser.add_argument(
            "--full-page",
            action=argparse.BooleanOptionalAction,
            default=True,
            help=(
                "Capture the full page instead of only the active viewport. "
                "Playwright supports this directly; Selenium falls back to a viewport capture."
            ),
        )
        parser.add_argument(
            "--ci-fast",
            action="store_true",
            help=(
                "Optimize for routine CI by forcing a single desktop Playwright/Chromium "
                "capture with faster load-state defaults."
            ),
        )

    def handle(self, *args, **options):
        """Capture screenshots for all requested paths and viewport profiles."""

        preview_username = ""
        preview_password = ""
        preview_user_id: int | None = None

        try:
            if options["username"] is not None or options["password"] is not None:
                self.stderr.write(
                    self.style.WARNING(
                        "--username and --password are legacy options and ignored. "
                        "Preview now uses a temporary admin account."
                    )
                )

            normalized_options = self._normalize_options(options)
            base_url = normalized_options["base_url"].rstrip("/")
            if normalized_options.get("wait_for_suite", False):
                self._wait_for_suite_ready(
                    base_url=base_url,
                    timeout_seconds=normalized_options.get("suite_timeout", 60),
                )

            if not normalized_options["no_login"]:
                preview_username, preview_password, preview_user_id = self._create_throwaway_admin_user()

            output = Path(normalized_options["output"])
            if not output.is_absolute():
                output = settings.BASE_DIR / output

            output_dir = Path(normalized_options["output_dir"]) if normalized_options["output_dir"] else output.parent
            if not output_dir.is_absolute():
                output_dir = settings.BASE_DIR / output_dir
            output_dir.mkdir(parents=True, exist_ok=True)

            default_admin_path = f"/{getattr(settings, 'ADMIN_URL_PATH', 'admin/').strip('/')}/"
            paths = normalized_options["paths"] or [default_admin_path]
            viewport_names = [item.strip() for item in normalized_options["viewports"].split(",") if item.strip()]
            if not viewport_names:
                raise CommandError("At least one viewport profile must be provided via --viewports.")

            invalid_viewports = sorted(set(viewport_names) - set(DEFAULT_VIEWPORTS))
            if invalid_viewports:
                raise CommandError(
                    "Unsupported viewport profile(s): "
                    + ", ".join(invalid_viewports)
                    + ". Supported values are: "
                    + ", ".join(DEFAULT_VIEWPORTS)
                )

            backends = [item.strip().lower() for item in normalized_options["backend"].split(",") if item.strip()]
            if not backends:
                raise CommandError("At least one backend must be provided via --backend.")

            invalid_backends = sorted(set(backends) - set(SUPPORTED_BACKENDS))
            if invalid_backends:
                raise CommandError(
                    "Unsupported backend(s): "
                    + ", ".join(invalid_backends)
                    + ". Supported values are: "
                    + ", ".join(SUPPORTED_BACKENDS)
                )

            engines = [item.strip().lower() for item in normalized_options["engine"].split(",") if item.strip()]
            if not engines:
                raise CommandError("At least one engine must be provided via --engine.")

            captures = self._build_capture_plan(
                paths=paths,
                viewport_names=viewport_names,
                output=output,
                output_dir=output_dir,
            )

            last_error: CommandError | None = None
            for backend in backends:
                try:
                    self._capture_with_backend(
                        backend=backend,
                        base_url=base_url,
                        username=preview_username,
                        password=preview_password,
                        captures=captures,
                        engines=engines,
                        login_required=not normalized_options["no_login"],
                        page_ready_state=normalized_options["page_ready_state"],
                        ready_selectors=normalized_options["ready_selectors"],
                        full_page=normalized_options["full_page"],
                    )
                    self._print_reports(captures)
                    return
                except CommandError as exc:
                    last_error = exc
                    self.stderr.write(f"Backend '{backend}' failed: {exc}")

            raise CommandError(f"All preview backends failed. Last error: {last_error}")
        finally:
            try:
                self._delete_throwaway_admin_user(preview_user_id)
            except DatabaseError as exc:
                self.stderr.write(
                    self.style.WARNING(
                        f"Failed to delete temporary preview user {preview_user_id}: {exc}"
                    )
                )
            except Exception as exc:
                self.stderr.write(
                    self.style.WARNING(
                        "Unexpected error while deleting temporary preview user "
                        f"{preview_user_id}: {exc}"
                    )
                )

    def _normalize_options(self, options: dict[str, object]) -> dict[str, object]:
        """Return effective preview options after applying convenience presets.

        Args:
            options (dict[str, object]): Parsed command options from Django.

        Returns:
            dict[str, object]: Normalized options used by the preview workflow.
        """

        normalized = dict(options)
        normalized.setdefault("page_ready_state", "networkidle")
        normalized.setdefault("ready_selectors", [])
        normalized.setdefault("full_page", True)
        normalized.setdefault("ci_fast", False)
        if normalized.get("ci_fast", False):
            normalized["backend"] = "playwright"
            normalized["engine"] = "chromium"
            normalized["viewports"] = "desktop"
            normalized["page_ready_state"] = "domcontentloaded"
            normalized["full_page"] = False
        return normalized

    def _build_capture_plan(
        self,
        *,
        paths: list[str],
        viewport_names: list[str],
        output: Path,
        output_dir: Path,
    ) -> list[dict[str, object]]:
        """Build a deterministic list of captures for path and viewport combinations."""

        captures: list[dict[str, object]] = []
        use_legacy_output = len(paths) == 1
        for path in paths:
            normalized_path = path if path.startswith("/") else f"/{path}"
            slug = self._path_slug(normalized_path)
            for viewport_name in viewport_names:
                viewport_size = DEFAULT_VIEWPORTS[viewport_name]
                if use_legacy_output and viewport_name == "desktop":
                    target = output
                else:
                    target = output_dir / f"{slug}-{viewport_name}.png"
                captures.append(
                    {
                        "path": normalized_path,
                        "viewport_name": viewport_name,
                        "viewport_size": viewport_size,
                        "output": target,
                    }
                )
        return captures

    def _path_slug(self, path: str) -> str:
        """Return a filesystem-safe slug for a capture path."""

        cleaned = path.strip("/") or "root"
        return re.sub(r"[^a-zA-Z0-9]+", "-", cleaned).strip("-") or "root"

    def _print_reports(self, captures: list[dict[str, object]]) -> None:
        """Analyze generated images and print a short diagnostic summary."""

        for capture in captures:
            output = capture["output"]
            report = analyze_preview_image(output)
            self.stdout.write(self.style.SUCCESS(f"Saved preview to: {output}"))
            self.stdout.write(
                f"Capture [{capture['path']}] ({capture['viewport_name']}): "
                f"size={report.width}x{report.height}, "
                f"brightness={report.mean_brightness}, "
                f"white_ratio={report.white_pixel_ratio}, "
                f"mostly_white={report.mostly_white()}"
            )

    def _assert_capture_outputs_exist(
        self, *, captures: list[dict[str, object]], attempt_label: str
    ) -> None:
        """Ensure the active backend actually produced every requested artifact.

        Args:
            captures (list[dict[str, object]]): Planned capture definitions whose
                ``output`` values should point at generated image files.
            attempt_label (str): Human-readable label for the active backend or
                engine attempt used in the capture pass.

        Returns:
            None: Returns once every requested artifact exists on disk.

        Raises:
            CommandError: If any expected screenshot artifact was not produced.
        """

        missing_outputs = [
            str(capture["output"])
            for capture in captures
            if not Path(capture["output"]).is_file()
        ]
        if not missing_outputs:
            return

        raise CommandError(
            f"Preview capture did not produce the expected screenshot artifact(s): "
            f"{', '.join(missing_outputs)}. Confirm the suite is reachable, then rerun "
            f"`manage.py preview` after starting `manage.py runserver`; if browser "
            f"automation is unavailable in this environment, use another configured "
            f"backend or capture a manual screenshot. Attempt: {attempt_label}."
        )

    def _clear_capture_outputs(self, *, captures: list[dict[str, object]]) -> None:
        """Remove planned capture outputs before retrying a backend or engine.

        Args:
            captures (list[dict[str, object]]): Planned capture definitions whose
                ``output`` files should be removed if they already exist.

        Returns:
            None: Returns after removing any pre-existing planned artifact files.
        """

        for capture in captures:
            output = Path(capture["output"])
            if output.exists():
                output.unlink()

    def _create_throwaway_admin_user(self) -> tuple[str, str, int]:
        """Create temporary admin credentials for preview login.

        Args:
            None.

        Returns:
            tuple[str, str, int]: Generated username, generated password, and user primary key.

        Raises:
            DatabaseError: If the database operation fails while creating the user.
            IntegrityError: If user creation violates a database constraint.

        Side Effects:
            Inserts a temporary superuser record that should be cleaned up with
            ``_delete_throwaway_admin_user`` after capture completes.
        """

        user_model = get_user_model()
        username = f"preview-{secrets.token_hex(6)}"
        password = secrets.token_urlsafe(18)
        user = user_model.objects.create_superuser(username=username, password=password)
        return username, password, user.pk

    def _delete_throwaway_admin_user(self, user_id: int | None) -> None:
        """Delete a temporary preview superuser when one was created.

        Args:
            user_id (int | None): Primary key of the temporary user, or ``None`` when
                no temporary account was created.

        Returns:
            None: Performs best-effort cleanup and silently ignores missing users.
        """

        if user_id is None:
            return

        user_model = get_user_model()
        try:
            user = user_model.objects.get(pk=user_id)
        except user_model.DoesNotExist:
            return
        user.delete()

    def _capture_with_backend(
        self,
        *,
        backend: str,
        base_url: str,
        username: str,
        password: str,
        captures: list[dict[str, object]],
        engines: list[str],
        login_required: bool,
        page_ready_state: str,
        ready_selectors: list[str],
        full_page: bool,
    ) -> None:
        """Capture screenshots using the selected backend and engine fallback order."""

        if backend == "playwright":
            self._capture_with_playwright(
                base_url=base_url,
                username=username,
                password=password,
                captures=captures,
                engines=engines,
                login_required=login_required,
                page_ready_state=page_ready_state,
                ready_selectors=ready_selectors,
                full_page=full_page,
            )
            return

        if backend == "selenium":
            self._capture_with_selenium(
                base_url=base_url,
                username=username,
                password=password,
                captures=captures,
                engines=engines,
                login_required=login_required,
                page_ready_state=page_ready_state,
                ready_selectors=ready_selectors,
                full_page=full_page,
            )
            return

        raise CommandError(f"Unsupported backend '{backend}'.")

    def _wait_for_suite_ready(self, *, base_url: str, timeout_seconds: int) -> None:
        """Wait until the preview suite base URL responds before capturing.

        Args:
            base_url (str): Absolute base URL used by the preview command.
            timeout_seconds (int): Maximum time to wait for an HTTP response.

        Returns:
            None: Returns once the suite responds to an HTTP request.

        Raises:
            CommandError: If timeout is not positive or the suite is not reachable
                before the timeout expires.
        """

        if timeout_seconds <= 0:
            raise CommandError("--suite-timeout must be greater than zero.")

        deadline = time.monotonic() + timeout_seconds
        last_error: URLError | None = None

        while time.monotonic() < deadline:
            try:
                with urlopen(base_url, timeout=5):
                    self.stdout.write(self.style.SUCCESS(f"Suite is reachable at {base_url}."))
                    return
            except HTTPError:
                # HTTP errors still indicate that the web server is running.
                self.stdout.write(self.style.SUCCESS(f"Suite is reachable at {base_url}."))
                return
            except URLError as exc:
                last_error = exc
                time.sleep(1)

        raise CommandError(
            "Timed out waiting for suite to become reachable at "
            f"{base_url} after {timeout_seconds}s. Last error: {last_error}"
        )

    def _capture_with_playwright(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        captures: list[dict[str, object]],
        engines: list[str],
        login_required: bool,
        page_ready_state: str,
        ready_selectors: list[str],
        full_page: bool,
    ) -> None:
        """Capture screenshots using Playwright engines in fallback order."""

        last_error: CommandError | None = None
        for engine in engines:
            try:
                self._clear_capture_outputs(captures=captures)
                self._capture_all_playwright(
                    base_url=base_url,
                    username=username,
                    password=password,
                    captures=captures,
                    engine=engine,
                    login_required=login_required,
                    page_ready_state=page_ready_state,
                    ready_selectors=ready_selectors,
                    full_page=full_page,
                )
                self._assert_capture_outputs_exist(
                    captures=captures,
                    attempt_label=f"playwright/{engine}",
                )
                return
            except CommandError as exc:
                last_error = exc
                self.stderr.write(f"Playwright engine '{engine}' failed: {exc}")

        raise CommandError(f"All Playwright engines failed. Last error: {last_error}")

    def _capture_all_playwright(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        captures: list[dict[str, object]],
        engine: str,
        login_required: bool,
        page_ready_state: str,
        ready_selectors: list[str],
        full_page: bool,
    ) -> None:
        """Use Playwright to login once and capture all requested screenshots."""

        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise CommandError(
                "Playwright is required for this backend. "
                f"Install it for this interpreter ({sys.executable}) and run "
                "`python -m playwright install chromium firefox` (or `./env-refresh.sh --deps-only`)."
            ) from exc

        admin_path = getattr(settings, "ADMIN_URL_PATH", "admin/").strip("/")
        login_url = f"{base_url}/{admin_path}/login/"

        try:
            with sync_playwright() as playwright:
                launcher = getattr(playwright, engine)
                browser = launcher.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                if login_required:
                    page.goto(login_url, wait_until=page_ready_state)
                    page.fill("#id_username", username)
                    page.fill("#id_password", password)
                    page.click("input[type='submit']")
                    page.wait_for_load_state(page_ready_state)
                    self._validate_login_success(page.url, login_url)

                for capture in captures:
                    width, height = capture["viewport_size"]
                    output = capture["output"]
                    output.parent.mkdir(parents=True, exist_ok=True)
                    page.set_viewport_size({"width": width, "height": height})
                    page.goto(f"{base_url}{capture['path']}", wait_until=page_ready_state)
                    for selector in ready_selectors:
                        page.wait_for_selector(selector)
                    page.screenshot(path=str(output), full_page=full_page)

                context.close()
                browser.close()
        except AttributeError as exc:
            raise CommandError(f"Unsupported Playwright engine '{engine}'.") from exc
        except PlaywrightError as exc:
            raise CommandError(self._playwright_runtime_help(exc)) from exc

    def _capture_with_selenium(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        captures: list[dict[str, object]],
        engines: list[str],
        login_required: bool,
        page_ready_state: str,
        ready_selectors: list[str],
        full_page: bool,
    ) -> None:
        """Capture screenshots using Selenium browser fallback derived from engine order."""

        browsers = [ENGINE_TO_SELENIUM_BROWSER[engine] for engine in engines if engine in ENGINE_TO_SELENIUM_BROWSER]
        if not browsers:
            raise CommandError(
                "No Selenium-compatible engines were provided. "
                "Use --engine with chromium and/or firefox when backend includes selenium."
            )

        last_error: CommandError | None = None
        for browser_name in browsers:
            try:
                self._clear_capture_outputs(captures=captures)
                self._capture_all_selenium(
                    base_url=base_url,
                    username=username,
                    password=password,
                    captures=captures,
                    browser_name=browser_name,
                    login_required=login_required,
                    page_ready_state=page_ready_state,
                    ready_selectors=ready_selectors,
                    full_page=full_page,
                )
                self._assert_capture_outputs_exist(
                    captures=captures,
                    attempt_label=f"selenium/{browser_name}",
                )
                return
            except CommandError as exc:
                last_error = exc
                self.stderr.write(f"Selenium browser '{browser_name}' failed: {exc}")

        raise CommandError(f"All Selenium browsers failed. Last error: {last_error}")

    def _capture_all_selenium(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        captures: list[dict[str, object]],
        browser_name: str,
        login_required: bool,
        page_ready_state: str,
        ready_selectors: list[str],
        full_page: bool,
    ) -> None:
        """Use Selenium WebDriver to login once and capture requested screenshots."""

        try:
            from selenium import webdriver
            from selenium.common.exceptions import TimeoutException, WebDriverException
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
        except ModuleNotFoundError as exc:
            raise CommandError(
                "Selenium is required for this backend. Install it with "
                f"`{sys.executable} -m pip install selenium`."
            ) from exc

        admin_path = getattr(settings, "ADMIN_URL_PATH", "admin/").strip("/")
        login_url = f"{base_url}/{admin_path}/login/"

        driver = None
        try:
            if browser_name == "chrome":
                from selenium.webdriver.chrome.options import Options as ChromeOptions

                options = ChromeOptions()
                options.add_argument("--headless=new")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--no-sandbox")
                driver = webdriver.Chrome(options=options)
            elif browser_name == "firefox":
                from selenium.webdriver.firefox.options import Options as FirefoxOptions

                options = FirefoxOptions()
                options.add_argument("--headless")
                driver = webdriver.Firefox(options=options)
            else:
                raise CommandError(f"Unsupported Selenium browser '{browser_name}'.")

            if login_required:
                driver.get(login_url)
                driver.find_element(By.ID, "id_username").send_keys(username)
                driver.find_element(By.ID, "id_password").send_keys(password)
                driver.find_element(By.CSS_SELECTOR, "input[type='submit']").click()
                try:
                    WebDriverWait(driver, 20).until(
                        lambda current_driver: urlparse(current_driver.current_url).path.rstrip("/")
                        != urlparse(login_url).path.rstrip("/")
                    )
                except TimeoutException:
                    pass
                self._validate_login_success(driver.current_url, login_url)

            if page_ready_state == "networkidle":
                self.stderr.write(
                    self.style.WARNING(
                        "Selenium does not support waiting for networkidle; treating "
                        "--page-ready-state=networkidle as load."
                    )
                )
            if full_page:
                self.stderr.write(
                    self.style.WARNING(
                        "Selenium does not support true full-page screenshots; treating "
                        "--full-page as a viewport capture."
                    )
                )

            for capture in captures:
                width, height = capture["viewport_size"]
                output = capture["output"]
                output.parent.mkdir(parents=True, exist_ok=True)
                driver.set_window_size(width, height)
                driver.get(f"{base_url}{capture['path']}")
                WebDriverWait(driver, 20).until(
                    lambda current_driver: current_driver.execute_script("return document.readyState") == "complete"
                )
                for selector in ready_selectors:
                    WebDriverWait(driver, 20).until(
                        lambda current_driver, css=selector: current_driver.find_elements(By.CSS_SELECTOR, css)
                    )
                png_data = driver.get_screenshot_as_png()
                output.write_bytes(png_data)
        except (TimeoutException, WebDriverException) as exc:
            raise CommandError(self._selenium_runtime_help(exc)) from exc
        finally:
            if driver is not None:
                driver.quit()

    def _validate_login_success(self, current_url: str, login_url: str) -> None:
        """Validate that authentication left the login endpoint.

        Args:
            current_url (str): Browser URL after submitting the login form.
            login_url (str): Expected login URL used for authentication.

        Returns:
            None: Returns when login redirected away from the login page.

        Raises:
            CommandError: If current URL path still matches the login URL path.
        """

        if urlparse(current_url).path.rstrip("/") == urlparse(login_url).path.rstrip("/"):
            raise CommandError(
                "Preview login did not complete successfully. "
                "If you intended to capture anonymous pages, pass --no-login."
            )

    def _playwright_runtime_help(self, exc: Exception) -> str:
        """Build user-facing guidance for common Playwright runtime failures.

        Args:
            exc (Exception): Original runtime exception from Playwright.

        Returns:
            str: Error text augmented with actionable troubleshooting guidance.
        """

        base_message = str(exc)
        lower_message = base_message.lower()

        if "host system is missing dependencies" in lower_message:
            return (
                f"{base_message}\n"
                "Playwright browser binaries were found, but OS libraries are missing. "
                "Run `python -m playwright install-deps` (or install the listed apt packages) "
                "for this machine."
            )

        if "executable doesn't exist" in lower_message or "browser has been closed" in lower_message:
            return (
                f"{base_message}\n"
                "The Playwright runtime for this interpreter appears incomplete. "
                f"Use `{sys.executable} -m playwright install chromium firefox` "
                "or `./env-refresh.sh --deps-only` to re-install runtimes for the active environment."
            )

        return base_message

    def _selenium_runtime_help(self, exc: Exception) -> str:
        """Build user-facing guidance for common Selenium runtime failures.

        Args:
            exc (Exception): Original runtime exception from Selenium.

        Returns:
            str: Error text augmented with troubleshooting guidance.
        """

        return (
            f"{exc}\n"
            "Selenium could not start a browser in this environment. "
            "Install browser binaries and WebDriver dependencies, or prioritize the playwright backend."
        )
