"""Capture deterministic admin previews and emit lightweight image diagnostics."""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

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
        parser.add_argument("--username", default="admin", help="Deterministic admin username.")
        parser.add_argument("--password", default="admin123", help="Deterministic admin password.")
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

    def handle(self, *args, **options):
        """Capture screenshots for all requested paths and viewport profiles."""

        self._ensure_admin_user(username=options["username"], password=options["password"])

        output = Path(options["output"])
        if not output.is_absolute():
            output = settings.BASE_DIR / output

        output_dir = Path(options["output_dir"]) if options["output_dir"] else output.parent
        if not output_dir.is_absolute():
            output_dir = settings.BASE_DIR / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        paths = options["paths"] or ["/admin/"]
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

        captures = self._build_capture_plan(paths=paths, viewport_names=viewport_names, output=output, output_dir=output_dir)

        last_error: Exception | None = None
        for engine in engines:
            try:
                self._capture_all(
                    base_url=options["base_url"].rstrip("/"),
                    username=options["username"],
                    password=options["password"],
                    captures=captures,
                    engine=engine,
                )
                self._print_reports(captures)
                return
            except Exception as exc:
                last_error = exc
                self.stderr.write(f"Engine '{engine}' failed: {exc}")

        raise CommandError(f"All preview engines failed. Last error: {last_error}")

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
        normalized_paths = [path if path.startswith("/") else f"/{path}" for path in paths]
        slugs = [self._path_slug(path) for path in normalized_paths]
        slug_counts: dict[str, int] = {}
        unique_slugs: list[str] = []
        for slug in slugs:
            slug_counts[slug] = slug_counts.get(slug, 0) + 1
            if slug_counts[slug] == 1:
                unique_slugs.append(slug)
            else:
                unique_slugs.append(f"{slug}-{slug_counts[slug]}")

        for normalized_path, unique_slug in zip(normalized_paths, unique_slugs):
            for viewport_name in viewport_names:
                viewport_size = DEFAULT_VIEWPORTS[viewport_name]
                if use_legacy_output and viewport_name == "desktop":
                    target = output
                else:
                    target = output_dir / f"{unique_slug}-{viewport_name}.png"
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

    def _capture_all(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        captures: list[dict[str, object]],
        engine: str,
    ) -> None:
        """Use Playwright to login once and capture all requested screenshots."""
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise CommandError(
                "Playwright is required for preview. Install it and run `python -m playwright install chromium firefox`."
            ) from exc

        login_url = f"{base_url}/admin/login/"

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
            raise CommandError(str(exc)) from exc
