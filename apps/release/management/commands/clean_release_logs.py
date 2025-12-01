"""Management command to remove release publish logs and lock files."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.release.models import PackageRelease


class Command(BaseCommand):
    help = (
        "Remove release publish logs and associated lock files so the flow can restart."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "releases",
            nargs="*",
            metavar="PACKAGE:VERSION",
            help="Release identifier in the form <package>:<version>.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="clean_all",
            help="Remove all release publish logs and related lock files.",
        )

    def handle(self, *args, **options):
        releases: list[str] = options.get("releases") or []
        clean_all: bool = options.get("clean_all", False)

        if not releases and not clean_all:
            raise CommandError(
                "Specify --all or at least one PACKAGE:VERSION identifier to clean."
            )

        log_dir = Path(settings.LOG_DIR)
        lock_dir = Path(settings.BASE_DIR) / ".locks"

        log_targets: set[Path] = set()
        lock_targets: set[Path] = set()

        if clean_all:
            log_targets.update(log_dir.glob("pr.*.log"))
            lock_targets.update(lock_dir.glob("release_publish_*.json"))
            lock_targets.update(lock_dir.glob("release_publish_*.restarts"))

        for spec in releases:
            release = self._resolve_release(spec)
            prefix = f"pr.{release.package.name}.v{release.version}"
            log_targets.update(log_dir.glob(f"{prefix}*.log"))
            for suffix in (".json", ".restarts"):
                lock_targets.add(lock_dir / f"release_publish_{release.pk}{suffix}")

        removed_logs = self._remove_files(log_targets)
        removed_locks = self._remove_files(lock_targets)

        if removed_logs:
            self.stdout.write(
                self.style.SUCCESS(f"Removed {removed_logs} release log file(s).")
            )
        else:
            self.stdout.write("No release log files removed.")

        if removed_locks:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Removed {removed_locks} release publish lock file(s)."
                )
            )
        else:
            self.stdout.write("No release publish lock files removed.")

    def _resolve_release(self, spec: str) -> PackageRelease:
        if ":" not in spec:
            raise CommandError(
                f"Release identifier '{spec}' is invalid. Use the format PACKAGE:VERSION."
            )
        package_name, version = [part.strip() for part in spec.split(":", 1)]
        if not package_name or not version:
            raise CommandError(
                f"Release identifier '{spec}' is invalid. Use the format PACKAGE:VERSION."
            )
        try:
            return PackageRelease.objects.select_related("package").get(
                package__name=package_name, version=version
            )
        except PackageRelease.DoesNotExist as exc:
            raise CommandError(
                f"Release for package '{package_name}' and version '{version}' not found."
            ) from exc

    def _remove_files(self, paths: set[Path]) -> int:
        removed = 0
        for path in paths:
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
            except OSError as exc:
                raise CommandError(f"Failed to remove {path}: {exc}") from exc
        return removed
