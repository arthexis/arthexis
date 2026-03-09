"""Capture deterministic Django admin previews and emit lightweight image diagnostics."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.playwright.preview_tool import analyze_preview_image


class Command(BaseCommand):
    """Login to Django admin with deterministic credentials and capture a screenshot."""

    help = "Capture an admin preview screenshot and print a simple image health summary."

    def add_arguments(self, parser):
        """Register CLI arguments for the preview command."""

        parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Server base URL.")
        parser.add_argument("--path", default="/admin/", help="Admin path to capture after login.")
        parser.add_argument("--username", default="admin", help="Deterministic admin username.")
        parser.add_argument("--password", default="admin123", help="Deterministic admin password.")
        parser.add_argument(
            "--output",
            default="media/previews/admin-preview.png",
            help="Relative or absolute PNG output path.",
        )
        parser.add_argument(
            "--engine",
            default="chromium,firefox",
            help="Comma-separated engine fallback order (chromium,firefox,webkit).",
        )

    def handle(self, *args, **options):
        """Capture an admin screenshot and print diagnostics."""

        self._ensure_admin_user(username=options["username"], password=options["password"])

        output = Path(options["output"])
        if not output.is_absolute():
            output = settings.BASE_DIR / output
        output.parent.mkdir(parents=True, exist_ok=True)

        engines = [item.strip() for item in options["engine"].split(",") if item.strip()]
        if not engines:
            raise CommandError("At least one engine must be provided via --engine.")

        last_error: Exception | None = None
        for engine in engines:
            try:
                self._capture(
                    base_url=options["base_url"].rstrip("/"),
                    path=options["path"],
                    username=options["username"],
                    password=options["password"],
                    output=output,
                    engine=engine,
                )
                report = analyze_preview_image(output)
                self.stdout.write(self.style.SUCCESS(f"Saved preview to: {output}"))
                self.stdout.write(
                    "Image report: "
                    f"size={report.width}x{report.height}, "
                    f"brightness={report.mean_brightness}, "
                    f"white_ratio={report.white_pixel_ratio}, "
                    f"mostly_white={report.mostly_white()}"
                )
                return
            except Exception as exc:
                last_error = exc
                self.stderr.write(f"Engine '{engine}' failed: {exc}")

        raise CommandError(f"All preview engines failed. Last error: {last_error}")

    def _ensure_admin_user(self, *, username: str, password: str) -> None:
        """Create or update deterministic superuser credentials for preview automation."""

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(username=username, defaults={"is_staff": True, "is_superuser": True})
        if created:
            user.set_password(password)
            user.save(update_fields=["password"])
            return

        changed = False
        if not user.is_staff:
            user.is_staff = True
            changed = True
        if not user.is_superuser:
            user.is_superuser = True
            changed = True
        if not user.check_password(password):
            user.set_password(password)
            changed = True
        if changed:
            user.save()

    def _capture(self, *, base_url: str, path: str, username: str, password: str, output: Path, engine: str) -> None:
        """Use Playwright to login and save a screenshot from the requested admin page."""

        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise CommandError(
                "Playwright is required for preview. Install it and run `python -m playwright install chromium firefox`."
            ) from exc

        login_url = f"{base_url}/admin/login/"
        capture_url = f"{base_url}{path}"

        try:
            with sync_playwright() as playwright:
                launcher = getattr(playwright, engine)
                browser = launcher.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                page.goto(login_url, wait_until="networkidle")
                page.fill("#id_username", username)
                page.fill("#id_password", password)
                page.click("input[type='submit']")
                page.goto(capture_url, wait_until="networkidle")
                page.screenshot(path=str(output), full_page=True)
                context.close()
                browser.close()
        except AttributeError as exc:
            raise CommandError(f"Unsupported Playwright engine '{engine}'.") from exc
        except PlaywrightError as exc:
            raise CommandError(str(exc)) from exc
