from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand
from apps.core.management.deprecation import absorbed_into_command


@absorbed_into_command("video --list-streams")
class Command(BaseCommand):
    """Backward-compatible wrapper around the unified video command."""

    help = "Run snapshot and MJPEG stream diagnostics. Deprecated: use `video` sub-actions."

    def add_arguments(self, parser) -> None:
        """Register legacy debug arguments."""

        parser.add_argument("--list", action="store_true", help="List video devices and MJPEG streams.")
        parser.add_argument("--snapshot", action="store_true", help="Capture a snapshot from a video device.")
        parser.add_argument("--device", help="Video device ID, slug, or identifier to use for snapshot capture.")
        parser.add_argument("--refresh-devices", action="store_true", help="Refresh video devices before capturing snapshots.")
        parser.add_argument("--auto-enable", action="store_true", help="Enable the Video Camera feature if it is disabled.")
        parser.add_argument("--mjpeg", action="store_true", help="Capture a frame from MJPEG streams.")
        parser.add_argument("--stream", help="MJPEG stream slug or ID to capture.")
        parser.add_argument("--include-inactive", action="store_true", help="Include inactive MJPEG streams.")

    def handle(self, *args, **options) -> None:
        """Delegate legacy debug options to ``video`` command sub-actions."""

        self.stdout.write(self.style.WARNING("`video_debug` is deprecated; use `video` sub-actions."))

        if not any(options[key] for key in ("list", "snapshot", "mjpeg")):
            options["list"] = True

        if options["list"]:
            call_command(
                "video",
                "list",
                discover=options["refresh_devices"],
                list_streams=True,
                auto_enable=options["auto_enable"],
                include_inactive=options["include_inactive"],
            )

        if options["snapshot"]:
            call_command(
                "video",
                "snapshot",
                device=options.get("device"),
                discover=options["refresh_devices"],
                auto_enable=options["auto_enable"],
            )

        if options["mjpeg"]:
            call_command(
                "video",
                "mjpeg",
                stream=options.get("stream"),
                include_inactive=options["include_inactive"],
            )
