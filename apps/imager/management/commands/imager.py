"""Management command for Raspberry Pi image artifact workflows."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.imager.models import RaspberryPiImageArtifact
from apps.imager.services import ImagerBuildError, build_rpi4b_image


class Command(BaseCommand):
    """Build and list Raspberry Pi image artifacts for Arthexis."""

    help = "Build Raspberry Pi 4B image artifacts with Arthexis preloaded bootstrap scripts."

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

        subparsers.add_parser("list", help="List generated Raspberry Pi image artifacts.")

    def handle(self, *args, **options) -> None:
        """Dispatch command to selected action."""

        action = options["action"]
        if action == "build":
            self._handle_build(options)
            return
        if action == "list":
            self._handle_list()
            return
        raise CommandError(f"Unsupported action '{action}'.")

    def _handle_build(self, options: dict[str, object]) -> None:
        """Build a Raspberry Pi 4B image artifact and print summary metadata."""

        try:
            result = build_rpi4b_image(
                name=str(options["name"]),
                base_image_uri=str(options["base_image_uri"]),
                output_dir=Path(str(options["output_dir"])),
                download_base_uri=str(options["download_base_uri"]),
                git_url=str(options["git_url"]),
                customize=not bool(options["skip_customize"]),
            )
        except ImagerBuildError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Built image: {result.output_path}"))
        self.stdout.write(f"sha256={result.sha256}")
        self.stdout.write(f"size_bytes={result.size_bytes}")
        if result.download_uri:
            self.stdout.write(f"download_uri={result.download_uri}")

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
