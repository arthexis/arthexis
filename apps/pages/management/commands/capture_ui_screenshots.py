"""Management command to capture UI screenshots defined by specs."""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.pages.screenshot_specs import (
    ScreenshotSpecRunner,
    ScreenshotUnavailable,
    autodiscover,
    registry,
)


class Command(BaseCommand):
    help = "Capture UI screenshots using registered screenshot specs."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--spec",
            action="append",
            dest="specs",
            default=[],
            help="Run a specific screenshot spec (can be provided multiple times)",
        )
        parser.add_argument(
            "--output-dir",
            default="artifacts/ui-screens",
            help="Directory where screenshot artifacts will be written.",
        )

    def handle(self, *args, **options):
        autodiscover()
        slugs: list[str] = options["specs"] or []
        specs = registry.all() if not slugs else [registry.get(slug) for slug in slugs]
        specs = sorted(specs, key=lambda spec: spec.slug)
        if not specs:
            self.stdout.write("No screenshot specs registered.")
            return
        output_dir = Path(options["output_dir"]).resolve()
        with ScreenshotSpecRunner(output_dir) as runner:
            for spec in specs:
                try:
                    result = runner.run(spec)
                except ScreenshotUnavailable as exc:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skipping manual screenshot for '{spec.slug}': {exc}"
                        )
                    )
                    continue
                except Exception as exc:  # pragma: no cover - defensive
                    raise CommandError(str(exc)) from exc
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Captured '{spec.slug}' to {result.image_path.relative_to(output_dir)}"
                    )
                )
                self.stdout.write(f"Base64 artifact stored at {result.base64_path}")
