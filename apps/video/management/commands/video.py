from __future__ import annotations

import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.video.frame_cache import get_frame, get_frame_cache, get_status
from apps.video.models import MjpegStream, VideoDevice
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
        parser.add_argument(
            "--doctor",
            action="store_true",
            help="Run server-side video diagnostics.",
        )

    def handle(self, *args, **options) -> None:
        if options["doctor"]:
            self._run_doctor()
            return

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

    def _run_doctor(self) -> None:
        """Run server-side checks for video streaming diagnostics."""

        self.stdout.write(self.style.MIGRATE_HEADING("Video Doctor"))
        node = Node.get_local()
        if node is None:
            self.stdout.write(
                self.style.WARNING(
                    "Local node is not registered; node-specific checks skipped."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Local node: {node.hostname} (id={node.pk})")
            )

        feature = NodeFeature.objects.filter(slug="video-cam").first()
        if feature is None:
            self.stdout.write(
                self.style.WARNING("Video Camera feature is not configured.")
            )
        else:
            assigned = False
            if node is not None:
                assigned = NodeFeatureAssignment.objects.filter(
                    node=node, feature=feature
                ).exists()
            status_label = "enabled" if feature.is_enabled else "disabled"
            assignment_label = "assigned" if assigned else "not assigned"
            self.stdout.write(
                f"Video Camera feature: {status_label} ({assignment_label})"
            )

        camera_stack = "available" if has_rpi_camera_stack() else "missing"
        self.stdout.write(f"Camera stack: {camera_stack}")

        self._report_devices(node)
        self._report_streams()
        self._report_frame_cache_status()

    def _report_devices(self, node: Node | None) -> None:
        """Report configured video devices for the doctor output."""

        queryset = VideoDevice.objects.all()
        if node is not None:
            queryset = queryset.filter(node=node)
        count = queryset.count()
        self.stdout.write(f"Video devices: {count}")
        default_device = VideoDevice.get_default_for_node(node)
        if default_device:
            self.stdout.write(
                f"Default device: {default_device.pk} {default_device.display_name} "
                f"identifier={default_device.identifier}"
            )
        elif count:
            self.stdout.write("Default device: none configured")

    def _report_streams(self) -> None:
        """Report MJPEG stream counts for the doctor output."""

        total = MjpegStream.objects.count()
        active = MjpegStream.objects.filter(is_active=True).count()
        self.stdout.write(f"MJPEG streams: {active} active / {total} total")

    def _report_frame_cache_status(self) -> None:
        """Report Redis-backed frame cache connectivity and sample data."""

        if not settings.VIDEO_FRAME_REDIS_URL:
            self.stdout.write(
                self.style.WARNING(
                    "Frame cache: VIDEO_FRAME_REDIS_URL is not configured."
                )
            )
            return

        client = get_frame_cache()
        if not client:
            self.stdout.write(
                self.style.WARNING("Frame cache: unable to initialize Redis client.")
            )
            return

        try:
            client.ping()
        except Exception as exc:  # pragma: no cover - runtime dependency
            self.stdout.write(
                self.style.WARNING(f"Frame cache: Redis ping failed ({exc}).")
            )
            return

        self.stdout.write(self.style.SUCCESS("Frame cache: Redis reachable."))

        stream = MjpegStream.objects.filter(is_active=True).order_by("name").first()
        if not stream:
            self.stdout.write(
                self.style.WARNING("Frame cache: no active streams to sample.")
            )
            return

        cached = get_frame(stream)
        if cached and cached.captured_at:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Latest cached frame: stream={stream.slug} "
                    f"captured_at={cached.captured_at.isoformat()}"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"No cached frames for stream={stream.slug}."
                )
            )

        status_payload = get_status(stream) or {}
        last_error = status_payload.get("last_error")
        if last_error:
            self.stdout.write(
                self.style.WARNING(
                    f"Camera service error for stream={stream.slug}: {last_error}"
                )
            )

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
