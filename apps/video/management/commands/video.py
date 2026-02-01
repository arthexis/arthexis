from __future__ import annotations

import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.video.models import VideoDevice
from apps.video.utils import WORK_DIR, has_rpi_camera_stack


class Command(BaseCommand):
    """List video devices and capture sample videos."""

    help = "List video devices and optionally capture a short sample video."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--enable",
            action="store_true",
            help="Enable the Video Camera feature for the local node.",
        )
        parser.add_argument(
            "--disable",
            action="store_true",
            help="Disable the Video Camera feature for the local node.",
        )
        parser.add_argument(
            "--discover",
            action="store_true",
            help="Discover video devices before listing them.",
        )
        parser.add_argument(
            "--device",
            help="Video device ID, slug, or identifier to use for sampling.",
        )
        parser.add_argument(
            "--samples",
            type=int,
            help="Capture N frames and assemble a short video.",
        )
        parser.add_argument(
            "--sample",
            action="store_const",
            const=1,
            dest="samples",
            help="Capture a single frame and assemble a short video.",
        )

    def handle(self, *args, **options) -> None:
        if options["enable"] and options["disable"]:
            raise CommandError("Choose only one of --enable or --disable.")

        node = Node.get_local()
        needs_node = any(
            options[key] for key in ("enable", "disable", "discover", "samples")
        )
        if needs_node and node is None:
            raise CommandError("No local node is registered for this command.")

        feature = None
        if needs_node:
            try:
                feature = NodeFeature.objects.get(slug="video-cam")
            except NodeFeature.DoesNotExist as exc:
                raise CommandError(
                    "The Video Camera node feature is not configured."
                ) from exc

        if options["enable"]:
            NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
            self.stdout.write(
                self.style.SUCCESS("Enabled the Video Camera feature for the node.")
            )
            if not has_rpi_camera_stack():
                self.stdout.write(
                    self.style.WARNING(
                        "Raspberry Pi camera stack not detected; enabling anyway."
                    )
                )

        if options["disable"]:
            deleted, _ = NodeFeatureAssignment.objects.filter(
                node=node, feature=feature
            ).delete()
            if deleted:
                self.stdout.write(
                    self.style.SUCCESS(
                        "Disabled the Video Camera feature for the node."
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "Video Camera feature was not enabled for the node."
                    )
                )

        if options["discover"]:
            created, updated = VideoDevice.refresh_from_system(node=node)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Detected {created} new and {updated} existing video devices."
                )
            )

        self._list_devices(node)

        samples = options.get("samples")
        if samples is None:
            return

        if samples <= 0:
            raise CommandError("Samples must be a positive integer.")

        if not feature.is_enabled and not options["enable"]:
            self.stdout.write(
                self.style.WARNING("Video Camera feature is currently disabled.")
            )

        if not has_rpi_camera_stack():
            self.stdout.write(
                self.style.WARNING(
                    "Raspberry Pi camera stack not detected; capture may fail."
                )
            )

        device = self._resolve_video_device(node, options.get("device"))
        if device is None:
            raise CommandError("No video device is available for sampling.")

        output_path = self._capture_samples_video(device, samples)
        self.stdout.write(self.style.SUCCESS(f"Sample video saved to {output_path}"))

    def _list_devices(self, node: Node | None) -> None:
        queryset = VideoDevice.objects.all()
        if node is not None:
            queryset = queryset.filter(node=node)
        devices = queryset.order_by("identifier")

        self.stdout.write(f"Video devices: {devices.count()}")
        for device in devices:
            default_flag = " default" if device.is_default else ""
            self.stdout.write(
                f"- {device.pk} {device.display_name} "
                f"identifier={device.identifier}{default_flag}"
            )

    def _resolve_video_device(
        self, node: Node | None, device_identifier: str | None
    ) -> VideoDevice | None:
        queryset = VideoDevice.objects.all()
        if node:
            queryset = queryset.filter(node=node)

        if not queryset.exists():
            return None

        if not device_identifier:
            return queryset.order_by("-is_default", "pk").first()

        if device_identifier.isdigit():
            device = queryset.filter(pk=int(device_identifier)).first()
            if device:
                return device
        device = queryset.filter(slug=device_identifier).first()
        if device:
            return device
        return queryset.filter(identifier=device_identifier).first()

    def _capture_samples_video(self, device: VideoDevice, samples: int) -> Path:
        frames_dir, output_path = self._get_video_paths()
        frames_dir.mkdir(parents=True, exist_ok=True)

        for index in range(1, samples + 1):
            snapshot_path = device.capture_snapshot_path()
            target_path = frames_dir / f"frame-{index:03d}.jpg"
            shutil.copy2(snapshot_path, target_path)

        self._encode_video(frames_dir, output_path)
        return output_path

    def _get_video_paths(self) -> tuple[Path, Path]:
        timestamp = datetime.now(timezone.utc)
        token = uuid.uuid4().hex
        frames_dir = WORK_DIR / "video-samples" / f"{timestamp:%Y%m%d%H%M%S}-{token}"
        output_path = WORK_DIR / f"video-samples-{timestamp:%Y%m%d%H%M%S}-{token}.mp4"
        return frames_dir, output_path

    def _encode_video(self, frames_dir: Path, output_path: Path) -> None:
        tool_path = shutil.which("ffmpeg")
        if not tool_path:
            raise CommandError("ffmpeg is required to assemble the sample video.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        pattern = frames_dir / "frame-%03d.jpg"
        result = subprocess.run(
            [
                tool_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-framerate",
                "5",
                "-start_number",
                "1",
                "-i",
                str(pattern),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "Video encoding failed").strip()
            raise CommandError(error)
        if not output_path.exists():
            raise CommandError("Video encoding failed to create the output file.")
