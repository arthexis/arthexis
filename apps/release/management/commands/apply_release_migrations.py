"""Apply release migration bundles with verification and graceful fallback."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, call_command
from django.core.management.base import CommandParser
from django.core.management.base import CommandError


class BundleVerificationError(CommandError):
    """Raised when a release migration bundle cannot be verified."""

RELEASE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


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
    """Apply migrations from release bundles when possible."""

    help = (
        "Apply release migration bundle deltas for installed versions and fall back to "
        "`manage.py migrate` on bundle mismatch."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Register command arguments."""

        parser.add_argument("target_version", help="Target release version to apply migrations for")
        parser.add_argument(
            "--installed-version",
            dest="installed_version",
            help="Installed release version. Defaults to VERSION file.",
        )
        parser.add_argument(
            "--bundle-dir",
            dest="bundle_dir",
            help="Bundle directory. Defaults to releases/<target_version>/migrations.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Fail instead of falling back to migrate when bundle verification fails.",
        )
        parser.add_argument(
            "--skip-data-transforms",
            action="store_true",
            help="Skip deferred post-migration data transforms.",
        )

    def handle(self, *args, **options):
        """Apply bundle-based migrations with deterministic verification."""

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
                self._run_deferred_data_transforms(skip=bool(options.get("skip_data_transforms")))
                self.stdout.write(
                    self.style.SUCCESS(
                        "Installed version matches target; database state verified and synchronized."
                    )
                )
                return

            manifest = self._load_manifest(bundle_dir, installed_version, target_version)
            self._apply_manifest(manifest)
            call_command("migrate", "--check", stdout=self.stdout, stderr=self.stderr)
            self._run_deferred_data_transforms(skip=bool(options.get("skip_data_transforms")))
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
            self._run_deferred_data_transforms(skip=bool(options.get("skip_data_transforms")))

    def _resolve_installed_version(self, explicit_version: str | None) -> str:
        """Resolve installed version from explicit input or local VERSION file."""

        if explicit_version:
            return explicit_version.strip()

        version_file = Path(settings.BASE_DIR) / "VERSION"
        if not version_file.exists():
            raise BundleVerificationError("Installed version could not be determined: VERSION file missing")

        version = version_file.read_text(encoding="utf-8").strip()
        if not version:
            raise BundleVerificationError("Installed version could not be determined: VERSION file empty")
        return version

    def _resolve_bundle_dir(self, target_version: str, explicit_dir: str | None) -> Path:
        """Resolve migration bundle directory path."""

        if explicit_dir:
            bundle_dir = Path(explicit_dir).expanduser().resolve()
        else:
            bundle_dir = (Path(settings.BASE_DIR) / "releases" / target_version / "migrations").resolve()

        if not bundle_dir.exists():
            raise BundleVerificationError(f"Bundle directory not found: {bundle_dir}")
        return bundle_dir

    def _verify_bundle(self, bundle_dir: Path) -> None:
        """Verify checksum list and optional signature for migration bundle artifacts."""

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

    def _load_manifest(self, bundle_dir: Path, installed_version: str, target_version: str) -> dict[str, object]:
        """Load version-to-version migration manifest."""

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
        """Apply only required migration targets from the manifest delta path."""

        deltas = manifest.get("deltas", {})
        if not isinstance(deltas, dict):
            raise BundleVerificationError("Manifest deltas format is invalid")

        for app_label in sorted(deltas):
            migration_names = deltas[app_label]
            if not isinstance(migration_names, list):
                raise BundleVerificationError(
                    f"Manifest deltas for app '{app_label}' must be a list"
                )
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
        """Run deferred idempotent transforms that are safe outside migrations."""

        if skip:
            self.stdout.write("Skipping deferred data transforms.")
            return

        call_command(
            "run_release_data_transforms",
            "--max-batches",
            "1",
            stdout=self.stdout,
            stderr=self.stderr,
        )
