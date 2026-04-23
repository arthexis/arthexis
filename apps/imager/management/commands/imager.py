"""Management command for Raspberry Pi image artifact workflows."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.imager.models import RaspberryPiImageArtifact
from apps.imager.services import (
    DEFAULT_RECOVERY_SSH_USER,
    ImagerBuildError,
    build_rpi4b_image,
    list_block_devices,
    write_image_to_device,
)


class Command(BaseCommand):
    """Build and list Raspberry Pi image artifacts for Arthexis."""

    help = "Build and safely write Raspberry Pi 4B image artifacts."

    def add_arguments(self, parser) -> None:
        """Register command actions and options."""

        subparsers = parser.add_subparsers(dest="action", required=True)

        build_parser = subparsers.add_parser("build", help="Build a Raspberry Pi 4B image artifact.")
        build_parser.add_argument("--name", required=True, help="Artifact name, for example v0-5-0.")
        build_parser.add_argument(
            "--base-image-uri",
            required=True,
            help="Base Raspberry Pi OS image URI (file://, local path, or https://).",
        )
        build_parser.add_argument(
            "--output-dir",
            default="build/rpi-imager",
            help="Output directory for generated image artifacts.",
        )
        build_parser.add_argument(
            "--download-base-uri",
            default="",
            help="Base URI where the generated image will be hosted for remote deploy.",
        )
        build_parser.add_argument(
            "--git-url",
            default="https://github.com/arthexis/arthexis.git",
            help="Git repository used for first-boot Arthexis bootstrap.",
        )
        build_parser.add_argument(
            "--skip-customize",
            action="store_true",
            help="Copy the base image without injecting bootstrap scripts.",
        )
        build_parser.add_argument(
            "--build-engine",
            default="arthexis-bootstrap",
            help="Build engine backend used to produce the artifact.",
        )
        build_parser.add_argument(
            "--profile",
            default="bootstrap",
            help="Build profile for engine-specific validation and rollout metadata.",
        )
        build_parser.add_argument(
            "--profile-metadata",
            default="{}",
            help="JSON object carrying profile metadata, required artifacts, and rollout fields.",
        )
        build_parser.add_argument(
            "--recovery-ssh-user",
            default="",
            help=(
                "Recovery SSH username baked into the image when --recovery-authorized-key-file is used "
                f"(default: {DEFAULT_RECOVERY_SSH_USER})."
            ),
        )
        build_parser.add_argument(
            "--recovery-authorized-key-file",
            action="append",
            default=[],
            help="Path to a public-key file to authorize for recovery SSH access. May be repeated.",
        )

        subparsers.add_parser("devices", help="List candidate block devices for image writing.")
        subparsers.add_parser("list", help="List generated Raspberry Pi image artifacts.")

        write_parser = subparsers.add_parser(
            "write",
            help="Write an existing image artifact (or local image path) to a block device.",
        )
        write_parser.add_argument("--artifact", default="", help="Registered artifact name to write.")
        write_parser.add_argument(
            "--image-path",
            default="",
            help="Direct local path to an image file to write (alternative to --artifact).",
        )
        write_parser.add_argument("--device", required=True, help="Target block device path, for example /dev/sdb.")
        write_parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm destructive write operation.",
        )

    def handle(self, *args, **options) -> None:
        """Dispatch command to selected action."""

        action = options["action"]
        if action == "build":
            self._handle_build(options)
            return
        if action == "list":
            self._handle_list()
            return
        if action == "devices":
            self._handle_devices()
            return
        if action == "write":
            self._handle_write(options)
            return
        raise CommandError(f"Unsupported action '{action}'.")

    def _handle_build(self, options: dict[str, object]) -> None:
        """Build a Raspberry Pi 4B image artifact and print summary metadata."""

        try:
            profile_metadata = json.loads(str(options["profile_metadata"]))
        except json.JSONDecodeError as exc:
            raise CommandError("--profile-metadata must be valid JSON.") from exc
        if not isinstance(profile_metadata, dict):
            raise CommandError("--profile-metadata must decode to a JSON object.")

        recovery_authorized_keys = self._read_recovery_authorized_keys(
            [str(path) for path in options.get("recovery_authorized_key_file", [])]
        )
        recovery_ssh_user = str(options["recovery_ssh_user"]).strip()
        if recovery_authorized_keys:
            recovery_ssh_user = recovery_ssh_user or DEFAULT_RECOVERY_SSH_USER

        try:
            result = build_rpi4b_image(
                name=str(options["name"]),
                base_image_uri=str(options["base_image_uri"]),
                output_dir=Path(str(options["output_dir"])),
                download_base_uri=str(options["download_base_uri"]),
                git_url=str(options["git_url"]),
                customize=not bool(options["skip_customize"]),
                build_engine=str(options["build_engine"]),
                profile=str(options["profile"]),
                profile_metadata=profile_metadata,
                recovery_ssh_user=recovery_ssh_user,
                recovery_authorized_keys=recovery_authorized_keys,
            )
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Built image: {result.output_path}"))
        self.stdout.write(f"sha256={result.sha256}")
        self.stdout.write(f"size_bytes={result.size_bytes}")
        if result.download_uri:
            self.stdout.write(f"download_uri={result.download_uri}")

    def _read_recovery_authorized_keys(self, paths: list[str]) -> list[str]:
        """Load recovery authorized keys from one or more public-key files."""

        keys: list[str] = []
        for raw_path in paths:
            path = Path(raw_path).expanduser()
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                raise CommandError(
                    f"Could not read recovery authorized key file '{path}': {exc}"
                ) from exc
            for line in lines:
                normalized = line.strip()
                if not normalized or normalized.startswith("#"):
                    continue
                keys.append(normalized)

        if paths and not keys:
            raise CommandError("Recovery authorized key files did not contain any usable public keys.")
        return keys

    def _handle_list(self) -> None:
        """Print known Raspberry Pi image artifacts."""

        artifacts = RaspberryPiImageArtifact.objects.order_by("-created_at", "name")
        if not artifacts:
            self.stdout.write("No Raspberry Pi image artifacts are registered.")
            return
        for artifact in artifacts:
            self.stdout.write(
                f"{artifact.name} [{artifact.target}] file={artifact.output_filename} "
                f"sha256={artifact.sha256} uri={artifact.download_uri or '(not configured)'}"
            )

    def _handle_devices(self) -> None:
        """Print block devices and safety metadata for writing."""

        try:
            devices = list_block_devices()
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc

        if not devices:
            self.stdout.write("No block devices were discovered.")
            return
        for device in devices:
            mountpoints = ",".join(device.mountpoints) if device.mountpoints else "(none)"
            partitions = ",".join(device.partitions) if device.partitions else "(none)"
            self.stdout.write(
                f"{device.path} size={device.size_bytes} transport={device.transport or '(unknown)'} "
                f"removable={'yes' if device.removable else 'no'} protected={'yes' if device.protected else 'no'} "
                f"partitions={partitions} mounts={mountpoints}"
            )

    def _handle_write(self, options: dict[str, object]) -> None:
        """Write image artifact to block device with safety checks and verification."""

        artifact_name = str(options["artifact"])
        image_path = str(options["image_path"])
        if bool(artifact_name) == bool(image_path):
            raise CommandError("Provide exactly one of --artifact or --image-path.")

        try:
            result = write_image_to_device(
                device_path=str(options["device"]),
                artifact_name=artifact_name,
                image_path=image_path,
                confirmed=bool(options["yes"]),
            )
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Wrote {result.image_path} -> {result.device_path}"))
        self.stdout.write(f"size_bytes={result.size_bytes}")
        self.stdout.write(f"source_sha256={result.source_sha256}")
        self.stdout.write(f"written_sha256={result.written_sha256}")
        self.stdout.write(f"verified={'yes' if result.verified else 'no'}")
