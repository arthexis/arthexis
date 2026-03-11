"""Capture deterministic admin previews and emit lightweight image diagnostics."""

from __future__ import annotations

from pathlib import Path
import re
import secrets
import sys
from urllib.parse import urlparse

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
        parser.add_argument("--username", default=None, help="Deprecated; ignored.")
        parser.add_argument("--password", default=None, help="Deprecated; ignored.")
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
            "--engine",
            default="chromium,firefox",
            help="Comma-separated engine fallback order (chromium,firefox,webkit).",
        )
        parser.add_argument(
            "--no-login",
            action="store_true",
            help="Capture pages without authenticating first.",
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
                        "--username and --password are deprecated and ignored. "
                        "Preview now uses a temporary admin account."
                    )
                )

            if not options["no_login"]:
                preview_username, preview_password, preview_user_id = self._create_throwaway_admin_user()

            output = Path(options["output"])
            if not output.is_absolute():
                output = settings.BASE_DIR / output

            output_dir = Path(options["output_dir"]) if options["output_dir"] else output.parent
            if not output_dir.is_absolute():
                output_dir = settings.BASE_DIR / output_dir
            output_dir.mkdir(parents=True, exist_ok=True)

            default_admin_path = f"/{getattr(settings, 'ADMIN_URL_PATH', 'admin/').strip('/')}/"
            paths = options["paths"] or [default_admin_path]
            viewport_names = [item.strip() for item in options["viewports"].split(",") if item.strip()]
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

            engines = [item.strip() for item in options["engine"].split(",") if item.strip()]
            if not engines:
                raise CommandError("At least one engine must be provided via --engine.")

            captures = self._build_capture_plan(
                paths=paths,
                viewport_names=viewport_names,
                output=output,
                output_dir=output_dir,
            )

            last_error: CommandError | None = None
            for engine in engines:
                try:
                    self._capture_all(
                        base_url=options["base_url"].rstrip("/"),
                        username=preview_username,
                        password=preview_password,
                        captures=captures,
                        engine=engine,
                        login_required=not options["no_login"],
                    )
                    self._print_reports(captures)
                    return
                except CommandError as exc:
                    last_error = exc
                    self.stderr.write(f"Engine '{engine}' failed: {exc}")

            raise CommandError(f"All preview engines failed. Last error: {last_error}")
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

    def _capture_all(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        captures: list[dict[str, object]],
        engine: str,
        login_required: bool,
    ) -> None:
        """Use Playwright to login once and capture all requested screenshots."""
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise CommandError(
                "Playwright is required for preview. "
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
                    page.goto(login_url, wait_until="networkidle")
                    page.fill("#id_username", username)
                    page.fill("#id_password", password)
                    page.click("input[type='submit']")
                    page.wait_for_load_state("networkidle")
                    self._validate_login_success(page.url, login_url)

                for capture in captures:
                    width, height = capture["viewport_size"]
                    output = capture["output"]
                    output.parent.mkdir(parents=True, exist_ok=True)
                    page.set_viewport_size({"width": width, "height": height})
                    page.goto(f"{base_url}{capture['path']}", wait_until="networkidle")
                    page.screenshot(path=str(output), full_page=True)

                context.close()
                browser.close()
        except AttributeError as exc:
            raise CommandError(f"Unsupported Playwright engine '{engine}'.") from exc
        except PlaywrightError as exc:
            raise CommandError(self._playwright_runtime_help(exc)) from exc

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
