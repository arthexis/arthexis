"""Unified release management command."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.release.domain import capture_migration_state, prepare_release
from apps.release.models import PackageRelease
from apps.release.release import DEFAULT_PACKAGE, ReleaseError, build
from apps.release.models import Package as PackageModel

REQUIRED_PACKAGE_FIELDS = (
    "name",
    "description",
    "author",
    "email",
    "python_requires",
    "license",
    "repository_url",
    "homepage_url",
)


class Command(BaseCommand):
    """Run release-related actions from a single command entrypoint."""

    help = "Run release actions (prepare, build, capture-state, clean-logs, check-pypi)."

    def add_arguments(self, parser):
        """Register action subcommands and their options."""

        subparsers = parser.add_subparsers(dest="action", required=True)

        prepare_parser = subparsers.add_parser(
            "prepare", help="Prepare a release using the release domain helpers"
        )
        prepare_parser.add_argument("version", help="Version string for the release")

        build_parser = subparsers.add_parser(
            "build", help="Build the project and optionally upload to PyPI."
        )
        build_parser.add_argument("--bump", action="store_true", help="Increment patch version")
        build_parser.add_argument("--dist", action="store_true", help="Build distribution")
        build_parser.add_argument("--twine", action="store_true", help="Upload with Twine")
        build_parser.add_argument("--git", action="store_true", help="Commit and push changes")
        build_parser.add_argument("--tag", action="store_true", help="Create and push a git tag")
        build_parser.add_argument("--test", action="store_true", help="Run tests before building")
        build_parser.add_argument(
            "--all", action="store_true", help="Enable bump, dist, twine, git and tag"
        )
        build_parser.add_argument("--force", action="store_true", help="Skip PyPI version check")
        build_parser.add_argument(
            "--stash", action="store_true", help="Auto stash changes before building"
        )
        build_parser.add_argument(
            "--package", help="Build using the specified package (ID or name)"
        )

        capture_parser = subparsers.add_parser(
            "capture-state", help="Capture migration plan and schema artifacts for a release"
        )
        capture_parser.add_argument("version", help="Release version to snapshot")

        clean_parser = subparsers.add_parser(
            "clean-logs",
            help="Remove release publish logs and associated lock files so the flow can restart.",
        )
        clean_parser.add_argument(
            "releases",
            nargs="*",
            metavar="PACKAGE:VERSION",
            help="Release identifier in the form <package>:<version>.",
        )
        clean_parser.add_argument(
            "--all",
            action="store_true",
            dest="clean_all",
            help="Remove all release publish logs and related lock files.",
        )

        check_parser = subparsers.add_parser(
            "check-pypi", help="Run release.pypi health check for the requested release."
        )
        check_parser.add_argument(
            "release",
            nargs="?",
            help=(
                "Release primary key or version to check. "
                "Defaults to the latest release for the active package."
            ),
        )

    def handle(self, *args, **options):
        """Dispatch to the selected release action."""

        action = options["action"]
        if action == "prepare":
            return self._handle_prepare(options)
        if action == "build":
            return self._handle_build(options)
        if action == "capture-state":
            return self._handle_capture_state(options)
        if action == "clean-logs":
            return self._handle_clean_logs(options)
        if action == "check-pypi":
            return self._handle_check_pypi(options)
        raise CommandError(f"Unsupported action '{action}'.")

    def _handle_prepare(self, options: dict[str, object]) -> None:
        version = str(options["version"])
        try:
            prepare_release(version)
        except Exception as exc:  # pragma: no cover - orchestration wrapper
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Release {version} prepared"))

    def _handle_capture_state(self, options: dict[str, object]) -> None:
        version = str(options["version"])
        try:
            out_dir = capture_migration_state(version)
        except Exception as exc:  # pragma: no cover - orchestration wrapper
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Captured migration state in {out_dir}"))

    def _handle_build(self, options: dict[str, object]) -> int:
        package = self._get_package(options.get("package"))
        try:
            build(
                bump=bool(options["bump"]),
                tests=bool(options["test"]),
                dist=bool(options["dist"]),
                twine=bool(options["twine"]),
                git=bool(options["git"]),
                tag=bool(options["tag"]),
                all=bool(options["all"]),
                force=bool(options["force"]),
                stash=bool(options["stash"]),
                package=package,
            )
        except ReleaseError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return 1
        return 0

    def _handle_clean_logs(self, options: dict[str, object]) -> None:
        releases: list[str] = list(options.get("releases") or [])
        clean_all = bool(options.get("clean_all", False))

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
            self.stdout.write(self.style.SUCCESS(f"Removed {removed_logs} release log file(s)."))
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

    def _handle_check_pypi(self, options: dict[str, object]) -> None:
        call_command(
            "health",
            target=["release.pypi"],
            release=options.get("release"),
            stdout=self.stdout,
            stderr=self.stderr,
        )

    def _get_package(self, identifier):
        if not identifier:
            return DEFAULT_PACKAGE

        package_obj = self._resolve_package(str(identifier))
        self._validate_package(package_obj)
        return package_obj.to_package()

    def _resolve_package(self, identifier: str) -> PackageModel:
        query = PackageModel.objects.all()

        try:
            package_obj = query.get(pk=int(identifier))
        except (ValueError, PackageModel.DoesNotExist):
            try:
                package_obj = query.get(name=identifier)
            except PackageModel.DoesNotExist as exc:  # pragma: no cover - safeguard
                raise CommandError(f"Package '{identifier}' not found") from exc
        return package_obj

    def _validate_package(self, package_obj: PackageModel) -> None:
        missing = []
        for field in REQUIRED_PACKAGE_FIELDS:
            value = getattr(package_obj, field)
            if isinstance(value, str):
                value = value.strip()
            if not value:
                missing.append(package_obj._meta.get_field(field).verbose_name)

        if missing:
            readable = ", ".join(missing)
            raise CommandError(
                f"Package '{package_obj.name}' is missing required packaging configuration: {readable}."
            )

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
