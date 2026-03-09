"""Capture deterministic previews and emit lightweight image diagnostics."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.playwright.preview_tool import analyze_preview_image


class Command(BaseCommand):
    """Login with deterministic credentials and capture one or more screenshots."""

    help = "Capture preview screenshots and print a simple image health summary."

    def add_arguments(self, parser):
        """Register CLI arguments for the preview command."""

        parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Server base URL.")
        parser.add_argument(
            "--path",
            action="append",
            dest="paths",
            help="Path to capture after login. Repeat to capture multiple paths.",
        )
        parser.add_argument("--username", default="admin", help="Deterministic admin username.")
        parser.add_argument("--password", default="admin123", help="Deterministic admin password.")
        parser.add_argument(
            "--output",
            default="media/previews/admin-preview.png",
            help="PNG output path for single-path capture.",
        )
        parser.add_argument(
            "--output-dir",
            default="media/previews",
            help="Directory for multi-path capture outputs.",
        )
        parser.add_argument(
            "--engine",
            default="chromium,firefox",
            help="Comma-separated engine fallback order (chromium,firefox,webkit).",
        )

    def handle(self, *args, **options):
        """Capture screenshot(s) and print diagnostics for each artifact."""

        self._ensure_admin_user(username=options["username"], password=options["password"])

        paths = options.get("paths") or ["/admin/"]
        captures = self._build_capture_targets(paths=paths, output=options["output"], output_dir=options["output_dir"])

        engines = [item.strip() for item in options["engine"].split(",") if item.strip()]
        if not engines:
            raise CommandError("At least one engine must be provided via --engine.")

        for path, output in captures:
            output.parent.mkdir(parents=True, exist_ok=True)
            self._capture_with_fallback(
                base_url=options["base_url"].rstrip("/"),
                path=path,
                username=options["username"],
                password=options["password"],
                output=output,
                engines=engines,
            )
            report = analyze_preview_image(output)
            self.stdout.write(self.style.SUCCESS(f"Saved preview for {path} to: {output}"))
            self.stdout.write(
                "Image report: "
                f"size={report.width}x{report.height}, "
                f"brightness={report.mean_brightness}, "
                f"white_ratio={report.white_pixel_ratio}, "
                f"mostly_white={report.mostly_white()}"
            )

    def _build_capture_targets(
        self,
        *,
        paths: list[str],
        output: str,
        output_dir: str,
    ) -> list[tuple[str, Path]]:
        """Build target output files for either single- or multi-path capture mode."""

        if len(paths) == 1:
            target = Path(output)
            if not target.is_absolute():
                target = settings.BASE_DIR / target
            return [(paths[0], target)]

        base_output_dir = Path(output_dir)
        if not base_output_dir.is_absolute():
            base_output_dir = settings.BASE_DIR / base_output_dir

        targets: list[tuple[str, Path]] = []
        for raw_path in paths:
            slug = raw_path.strip("/").replace("/", "-") or "root"
            targets.append((raw_path, base_output_dir / f"{slug}.png"))
        return targets

    def _capture_with_fallback(
        self,
        *,
        base_url: str,
        path: str,
        username: str,
        password: str,
        output: Path,
        engines: list[str],
    ) -> None:
        """Capture a screenshot by trying each engine in configured order."""

        last_error: Exception | None = None
        for engine in engines:
            try:
                self._capture(
                    base_url=base_url,
                    path=path,
                    username=username,
                    password=password,
                    output=output,
                    engine=engine,
                )
                return
            except Exception as exc:
                last_error = exc
                self.stderr.write(f"Engine '{engine}' failed for {path}: {exc}")

        raise CommandError(f"All preview engines failed for {path}. Last error: {last_error}")

    def _ensure_admin_user(self, *, username: str, password: str) -> None:
        """Create or update deterministic superuser credentials for preview automation."""

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={"is_staff": True, "is_superuser": True},
        )
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
        """Use Playwright to login and save a screenshot from the requested page."""

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
