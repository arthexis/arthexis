"""Unified release management command."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.release import DEFAULT_PACKAGE, ReleaseError, build
from apps.release.domain import (
    capture_migration_state,
    list_transform_names,
    prepare_release,
    run_transform,
)
from apps.release.models import Package as PackageModel
from apps.release.models import PackageRelease

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
RELEASE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ACTION_ALIASES = {
    "clean": "clean-logs",
    "migrate": "apply-migrations",
    "snap": "capture-state",
    "snapshot": "capture-state",
    "transforms": "run-data-transforms",
    "xforms": "run-data-transforms",
}
BUILD_MODE_FLAGS = {
    "package": {"dist": True, "test": True},
    "publish": {"dist": True, "test": True, "twine": True},
    "release": {"dist": True, "git": True, "tag": True, "test": True, "twine": True},
}


class BundleVerificationError(CommandError):
    """Raised when a release migration bundle cannot be verified."""


def _resolve_signing_key() -> str:
    """Return configured HMAC signing key when available."""

    return (
        os.environ.get("RELEASE_BUNDLE_SIGNING_KEY")
        or os.environ.get("ARTHEXIS_RELEASE_BUNDLE_SIGNING_KEY")
        or ""
    ).strip()


def _assert_relative_to(base: Path, candidate: Path, *, label: str) -> None:
    """Ensure candidate stays within base directory."""

    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise BundleVerificationError(f"{label} escapes bundle directory: {candidate}") from exc


class Command(BaseCommand):
    """Run release-related actions from a single command entrypoint."""

    help = (
        "Run release actions (prepare, build, snap/capture-state, clean/clean-logs, "
        "check-pypi, migrate/apply-migrations, xforms/run-data-transforms)."
    )

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
        build_parser.add_argument(
            "--mode",
            choices=sorted(BUILD_MODE_FLAGS),
            help=(
                "Named workflow preset: package builds dists and runs tests; publish adds "
                "Twine upload; release adds git commit/push and tagging."
            ),
        )
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
            "capture-state",
            aliases=["snapshot", "snap"],
            help="snap/capture-state: capture migration plan and schema artifacts for a release",
        )
        capture_parser.add_argument("version", help="Release version to snapshot")

        clean_parser = subparsers.add_parser(
            "clean-logs",
            aliases=["clean"],
            help="clean/clean-logs: remove release publish logs and lock files so the flow can restart.",
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

        migration_parser = subparsers.add_parser(
            "apply-migrations",
            aliases=["migrate"],
            help=(
                "migrate/apply-migrations: apply release migration bundle deltas for installed "
                "versions and fall back to `manage.py migrate` on bundle mismatch."
            ),
        )
        migration_parser.add_argument("target_version", help="Target release version")
        migration_parser.add_argument(
            "--installed-version",
            dest="installed_version",
            help="Installed release version. Defaults to VERSION file.",
        )
        migration_parser.add_argument(
            "--bundle-dir",
            dest="bundle_dir",
            help="Bundle directory. Defaults to releases/<target_version>/migrations.",
        )
        migration_parser.add_argument(
            "--strict",
            action="store_true",
            help="Fail instead of falling back to migrate when bundle verification fails.",
        )
        migration_parser.add_argument(
            "--skip-data-transforms",
            action="store_true",
            help="Skip deferred post-migration data transforms.",
        )

        transforms_parser = subparsers.add_parser(
            "run-data-transforms",
            aliases=["transforms", "xforms"],
            help=(
                "xforms/run-data-transforms: run idempotent, checkpointed data transforms moved "
                "out of schema-critical migrations."
            ),
        )
        transforms_parser.add_argument(
            "transform",
            nargs="?",
            help="Optional transform name. Runs all registered transforms when omitted.",
        )
        transforms_parser.add_argument(
            "--max-batches",
            type=int,
            default=1,
            help="Number of batches to process for each transform.",
        )

    def handle(self, *args, **options):
        """Dispatch to the selected release action."""

        action = ACTION_ALIASES.get(options["action"], options["action"])
        handler_name = f"_handle_{action.replace('-', '_')}"
        handler = getattr(self, handler_name, None)
        if handler:
            return handler(options)

        # This path should be unreachable with `required=True` on the subparser,
        # but it serves as a safeguard.
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
        build_options = self._resolve_build_options(options)
        try:
            build(
                bump=build_options["bump"],
                tests=build_options["test"],
                dist=build_options["dist"],
                twine=build_options["twine"],
                git=build_options["git"],
                tag=build_options["tag"],
                all=bool(options["all"]),
                force=bool(options["force"]),
                stash=bool(options["stash"]),
                package=package,
            )
        except ReleaseError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return 1
        return 0

    def _resolve_build_options(self, options: dict[str, object]) -> dict[str, bool]:
        """Return normalized build flags after applying any named workflow preset."""

        build_options = {
            "bump": bool(options["bump"]),
            "dist": bool(options["dist"]),
            "git": bool(options["git"]),
            "tag": bool(options["tag"]),
            "test": bool(options["test"]),
            "twine": bool(options["twine"]),
        }

        mode = options.get("mode")
        if mode:
            build_options.update(BUILD_MODE_FLAGS.get(str(mode), {}))

        return build_options

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

    def _handle_run_data_transforms(self, options: dict[str, object]) -> None:
        max_batches = int(options["max_batches"])
        if max_batches < 1:
            raise CommandError("--max-batches must be >= 1")

        transform_name = options.get("transform")
        names = [str(transform_name)] if transform_name else list_transform_names()
        for name in names:
            self._run_transform_batches(name, max_batches=max_batches)

    def _handle_apply_migrations(self, options: dict[str, object]) -> None:
        target_version = str(options["target_version"]).strip()
        if not RELEASE_VERSION_PATTERN.fullmatch(target_version):
            raise BundleVerificationError(f"Invalid target version: {target_version!r}")
        installed_version = self._resolve_installed_version(options.get("installed_version"))
        if not RELEASE_VERSION_PATTERN.fullmatch(installed_version):
            raise BundleVerificationError(f"Invalid installed version: {installed_version!r}")
        bundle_dir = self._resolve_bundle_dir(target_version, options.get("bundle_dir"))

        try:
            self._verify_bundle(bundle_dir)
            if installed_version == target_version:
                call_command("migrate", "--noinput", stdout=self.stdout, stderr=self.stderr)
                call_command("migrate", "--check", stdout=self.stdout, stderr=self.stderr)
                self._run_deferred_data_transforms(skip=bool(options["skip_data_transforms"]))
                self.stdout.write(
                    self.style.SUCCESS(
                        "Installed version matches target; database state verified and synchronized."
                    )
                )
                return

            manifest = self._load_manifest(bundle_dir, installed_version, target_version)
            self._apply_manifest(manifest)
            call_command("migrate", "--check", stdout=self.stdout, stderr=self.stderr)
            self._run_deferred_data_transforms(skip=bool(options["skip_data_transforms"]))
            self.stdout.write(
                self.style.SUCCESS(
                    f"Applied migration bundle for {installed_version} -> {target_version}."
                )
            )
        except BundleVerificationError as exc:
            if bool(options.get("strict")):
                raise
            self.stderr.write(self.style.WARNING(f"{exc}. Falling back to Django migrate."))
            call_command("migrate", "--noinput", stdout=self.stdout, stderr=self.stderr)
            call_command("migrate", "--check", stdout=self.stdout, stderr=self.stderr)
            self._run_deferred_data_transforms(skip=bool(options["skip_data_transforms"]))

    def _run_transform_batches(self, transform_name: str, *, max_batches: int) -> None:
        for index in range(max_batches):
            try:
                result = run_transform(transform_name)
            except KeyError as exc:
                raise CommandError(str(exc)) from exc

            self.stdout.write(
                f"{transform_name}: batch={index + 1} processed={result.processed} "
                f"updated={result.updated} complete={result.complete}"
            )
            if result.complete:
                break

    def _resolve_installed_version(self, explicit_version: object) -> str:
        if explicit_version:
            return str(explicit_version).strip()

        version_file = Path(settings.BASE_DIR) / "VERSION"
        if not version_file.exists():
            raise BundleVerificationError("Installed version could not be determined: VERSION file missing")

        version = version_file.read_text(encoding="utf-8").strip()
        if not version:
            raise BundleVerificationError("Installed version could not be determined: VERSION file empty")
        return version

    def _resolve_bundle_dir(self, target_version: str, explicit_dir: object) -> Path:
        if explicit_dir:
            bundle_dir = Path(str(explicit_dir)).expanduser().resolve()
        else:
            bundle_dir = (Path(settings.BASE_DIR) / "releases" / target_version / "migrations").resolve()

        if not bundle_dir.exists():
            raise BundleVerificationError(f"Bundle directory not found: {bundle_dir}")
        return bundle_dir

    def _verify_bundle(self, bundle_dir: Path) -> None:
        checksum_file = bundle_dir / "checksums.sha256"
        if not checksum_file.exists():
            raise BundleVerificationError("Bundle checksum file is missing")

        for line in checksum_file.read_text(encoding="utf-8").splitlines():
            entry = line.strip()
            if not entry:
                continue
            parts = entry.split("  ", 1)
            if len(parts) != 2:
                raise BundleVerificationError(f"Malformed checksum line: {entry}")
            expected_digest, relative_path = parts
            artifact_path = (bundle_dir / relative_path).resolve()
            _assert_relative_to(bundle_dir, artifact_path, label="Bundle artifact path")
            if not artifact_path.exists():
                raise BundleVerificationError(f"Bundle artifact is missing: {relative_path}")
            actual_digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            if actual_digest != expected_digest:
                raise BundleVerificationError(f"Checksum mismatch for {relative_path}")

        signing_key = _resolve_signing_key()
        signature_file = bundle_dir / "checksums.sha256.sig"
        if signing_key and not signature_file.exists():
            raise BundleVerificationError("Bundle signature file is missing")

        if signing_key and signature_file.exists():
            expected_signature = hmac.new(
                signing_key.encode("utf-8"), checksum_file.read_bytes(), hashlib.sha256
            ).hexdigest()
            actual_signature = signature_file.read_text(encoding="utf-8").strip()
            if not hmac.compare_digest(expected_signature, actual_signature):
                raise BundleVerificationError("Bundle signature validation failed")

    def _load_manifest(
        self, bundle_dir: Path, installed_version: str, target_version: str
    ) -> dict[str, object]:
        manifest_path = bundle_dir / "manifests" / f"{installed_version}__to__{target_version}.json"
        if not manifest_path.exists():
            raise BundleVerificationError(
                f"Manifest for {installed_version} -> {target_version} is missing"
            )

        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BundleVerificationError(f"Manifest is not valid JSON: {manifest_path}") from exc

        deltas = payload.get("deltas")
        if not isinstance(deltas, dict):
            raise BundleVerificationError("Manifest payload is missing deltas map")
        return payload

    def _apply_manifest(self, manifest: dict[str, object]) -> None:
        deltas = manifest.get("deltas", {})
        if not isinstance(deltas, dict):
            raise BundleVerificationError("Manifest deltas format is invalid")

        for app_label in sorted(deltas):
            migration_names = deltas[app_label]
            if not isinstance(migration_names, list):
                raise BundleVerificationError(f"Manifest deltas for app '{app_label}' must be a list")
            if not migration_names:
                continue
            target_migration = str(migration_names[-1])
            call_command(
                "migrate",
                app_label,
                target_migration,
                "--noinput",
                stdout=self.stdout,
                stderr=self.stderr,
            )

    def _run_deferred_data_transforms(self, *, skip: bool) -> None:
        if skip:
            self.stdout.write("Skipping deferred data transforms.")
            return

        try:
            call_command(
                "release",
                "run-data-transforms",
                "--max-batches",
                "1",
                stdout=self.stdout,
                stderr=self.stderr,
            )
        except Exception as exc:  # pragma: no cover - defensive non-blocking path
            self.stderr.write(self.style.WARNING(f"Deferred data transforms failed: {exc}"))

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
