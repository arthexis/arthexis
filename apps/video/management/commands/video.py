from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q
from django.utils import timezone as django_timezone
from django.utils.dateparse import parse_datetime

from apps.nodes.feature_detection import is_feature_active_for_node
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.video.frame_cache import (
    CachedFrame,
    frame_cache_url,
    get_frame,
    get_frame_cache,
    get_status,
    store_frame,
    store_status,
)
from apps.video.models import MjpegDependencyError, MjpegStream, VideoDevice
from apps.video.utils import WORK_DIR, open_cv2_capture, probe_rpi_camera_stack

logger = logging.getLogger("apps.video.camera_service")


def _setting_default_float(name: str, fallback: float) -> float:
    """Return float setting value while preserving valid zero values."""

    configured = getattr(settings, name, fallback)
    return fallback if configured is None else float(configured)


class _StreamCapture:
    """Capture MJPEG frames from a single video stream device."""

    def __init__(self, stream: MjpegStream):
        self.stream = stream
        self._cv2 = None
        self._capture = None
        self._last_capture = 0.0
        self._last_error: str | None = None
        self._last_logged_error: str | None = None

    def _ensure_capture(self) -> bool:
        """Ensure an OpenCV capture handle exists and is opened."""

        if self._cv2 is None:
            self._cv2 = self.stream._load_cv2()
        if self._capture is None:
            self._capture = open_cv2_capture(
                self._cv2,
                self.stream.video_device.identifier,
            )
        if not self._capture.isOpened():
            self._capture.release()
            self._capture = None
            self._last_error = "Unable to open video device"
            return False
        return True

    def close(self) -> None:
        """Release any allocated OpenCV capture resources."""

        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def capture_frame(self, *, interval: float) -> bytes | None:
        """Capture and JPEG-encode the next frame when interval has elapsed."""

        now = time.monotonic()
        if (now - self._last_capture) < interval:
            return None
        if not self._ensure_capture():
            return None
        self._last_capture = now
        success, frame = self._capture.read()
        if not success:
            self._last_error = "Camera read failed"
            return None
        frame = self.stream._rotate_frame(frame, self._cv2)
        success, buffer = self._cv2.imencode(".jpg", frame)
        if not success:
            self._last_error = "JPEG encode failed"
            return None
        self._last_error = None
        return buffer.tobytes()

    def status_payload(self) -> dict[str, object]:
        """Build status payload for Redis-backed stream health reporting."""

        return {
            "stream": self.stream.slug,
            "device": self.stream.video_device.identifier,
            "last_error": self._last_error,
            "updated_at": django_timezone.now().isoformat(),
        }

    def log_status(self) -> None:
        """Log stream status transitions only when status changes."""

        if self._last_error == self._last_logged_error:
            return
        self._last_logged_error = self._last_error
        if self._last_error:
            logger.warning(
                "Camera service error for %s (%s): %s",
                self.stream.slug,
                self.stream.video_device.identifier,
                self._last_error,
            )
        else:
            logger.info(
                "Camera service recovered for %s (%s)",
                self.stream.slug,
                self.stream.video_device.identifier,
            )


class Command(BaseCommand):
    """List video devices and capture sample videos."""

    help = "List video devices and optionally capture a short sample video."
    _CAMERA_SERVICE_FRAME_TIMEOUT_SECONDS = 3.0
    _CAMERA_SERVICE_FRAME_POLL_SECONDS = 0.05
    _CAMERA_SERVICE_STATUS_STALE_SECONDS = 5.0
    _LEGACY_ACTION_FLAGS = {
        "--doctor": "doctor",
        "--mjpeg": "mjpeg",
        "--snapshot": "snapshot",
    }

    def _add_compatibility_flag(self, parser, *flags: str, **kwargs) -> None:
        """Register a legacy-only argument while keeping help output concise."""

        parser.add_argument(*flags, help=argparse.SUPPRESS, **kwargs)

    def _add_auto_enable_argument(self, parser) -> None:
        """Register the auto-enable feature toggle."""

        parser.add_argument(
            "--auto-enable",
            action="store_true",
            help="Enable the Video Camera feature automatically for active actions.",
        )

    def _add_device_selection_argument(self, parser) -> None:
        """Register the video device selector argument."""

        parser.add_argument(
            "--device",
            help="Video device ID, slug, or identifier to use for sampling.",
        )

    def _add_discovery_arguments(self, parser) -> None:
        """Register device discovery arguments for list-like actions."""

        parser.add_argument(
            "--discover",
            action="store_true",
            help="Discover video devices before listing them.",
        )
        parser.add_argument(
            "--refresh-devices",
            action="store_true",
            help="Alias for --discover.",
        )

    def _add_service_timing_arguments(self, parser) -> None:
        """Register camera service timing arguments."""

        parser.add_argument(
            "--interval",
            type=float,
            default=_setting_default_float("VIDEO_FRAME_CAPTURE_INTERVAL", 0.2),
            help="Seconds between frame capture attempts per stream.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=_setting_default_float("VIDEO_FRAME_SERVICE_SLEEP", 0.05),
            help="Seconds to sleep between capture loops.",
        )

    def _add_stream_selection_arguments(self, parser) -> None:
        """Register MJPEG stream selection arguments."""

        parser.add_argument(
            "--stream",
            help="MJPEG stream slug or ID to capture.",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="Include inactive MJPEG streams.",
        )

    def run_from_argv(self, argv: list[str]) -> None:
        """Rewrite legacy top-level action flags to the primary subcommand syntax."""

        rewritten_args = self._rewrite_legacy_cli_args(argv[2:])
        super().run_from_argv([*argv[:2], *rewritten_args])

    def add_arguments(self, parser) -> None:
        """Register supported command-line arguments."""

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
        self._add_discovery_arguments(parser)
        self._add_device_selection_argument(parser)
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
        self._add_auto_enable_argument(parser)
        self._add_compatibility_flag(parser, "--doctor", action="store_true", default=False)
        self._add_compatibility_flag(parser, "--mjpeg", action="store_true", default=False)
        self._add_compatibility_flag(parser, "--snapshot", action="store_true", default=False)
        self._add_compatibility_flag(parser, "--list-streams", action="store_true", default=False)
        self._add_compatibility_flag(parser, "--include-inactive", action="store_true", default=False)
        self._add_compatibility_flag(parser, "--stream")

        subparsers = parser.add_subparsers(dest="action")

        list_parser = subparsers.add_parser(
            "list",
            help="List video devices and optional stream details.",
        )
        self._add_discovery_arguments(list_parser)
        list_parser.add_argument("--list-streams", action="store_true", help="List MJPEG streams.")
        list_parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="Include inactive MJPEG streams.",
        )
        self._add_auto_enable_argument(list_parser)

        snapshot_parser = subparsers.add_parser(
            "snapshot",
            help="Capture a still snapshot from a video device.",
        )
        self._add_device_selection_argument(snapshot_parser)
        self._add_discovery_arguments(snapshot_parser)
        self._add_auto_enable_argument(snapshot_parser)

        mjpeg_parser = subparsers.add_parser(
            "mjpeg",
            help="Capture frame(s) from MJPEG stream cache.",
        )
        self._add_stream_selection_arguments(mjpeg_parser)

        subparsers.add_parser("doctor", help="Run server-side video diagnostics.")

        service_parser = subparsers.add_parser(
            "service",
            help="Run camera service capture loop.",
        )
        self._add_service_timing_arguments(service_parser)

    def _rewrite_legacy_cli_args(self, command_args: list[str]) -> list[str]:
        """Translate legacy CLI action flags into their preferred subcommand form.

        Root-level options must remain before the inserted subcommand so Django's
        parser still accepts legacy invocations such as ``video --verbosity 0
        --doctor``.
        """

        if not command_args or any(arg in {"-h", "--help"} for arg in command_args):
            return command_args
        if command_args[0] in {"list", "snapshot", "mjpeg", "doctor", "service"}:
            return command_args

        compatibility_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        for flag in self._LEGACY_ACTION_FLAGS:
            compatibility_parser.add_argument(flag, action="store_true")
        known, remainder = compatibility_parser.parse_known_args(command_args)

        action_flags = [
            action
            for flag, action in self._LEGACY_ACTION_FLAGS.items()
            if getattr(known, flag[2:].replace("-", "_"))
        ]
        if len(action_flags) != 1:
            return command_args

        action_flag = next(
            flag
            for flag, action in self._LEGACY_ACTION_FLAGS.items()
            if action == action_flags[0]
        )
        leading_root_args: list[str] = []
        trailing_args = list(remainder)
        while trailing_args:
            next_arg = trailing_args[0]
            if next_arg == action_flag:
                trailing_args.pop(0)
                continue
            if next_arg.startswith("-"):
                leading_root_args.append(trailing_args.pop(0))
                if trailing_args and not trailing_args[0].startswith("-"):
                    leading_root_args.append(trailing_args.pop(0))
                continue
            break

        return [*leading_root_args, action_flags[0], *trailing_args]

    def _normalize_compatibility_options(self, options: dict[str, object]) -> dict[str, object]:
        """Merge subcommand arguments with legacy compatibility flags."""

        normalized = dict(options)
        action = normalized.get("action")
        compatibility_actions = [
            compatibility_action
            for compatibility_action in self._LEGACY_ACTION_FLAGS.values()
            if normalized.get(compatibility_action)
        ]
        if action and compatibility_actions:
            raise CommandError(
                f"Cannot use subcommand '{action}' with legacy action flag "
                f"'--{compatibility_actions[0]}'."
            )
        if len(compatibility_actions) > 1:
            raise CommandError("Choose only one legacy action flag at a time.")

        normalized["discover"] = bool(normalized.get("discover") or normalized.get("refresh_devices"))
        normalized["doctor"] = bool(action == "doctor" or normalized.get("doctor"))
        normalized["mjpeg"] = bool(action == "mjpeg" or normalized.get("mjpeg"))
        normalized["snapshot"] = bool(action == "snapshot" or normalized.get("snapshot"))

        if normalized["doctor"]:
            normalized["action"] = "doctor"
        elif normalized["mjpeg"]:
            normalized["action"] = "mjpeg"
        elif normalized["snapshot"]:
            normalized["action"] = "snapshot"

        return normalized

    def handle(self, *args, **options) -> None:
        """Execute video camera management actions."""

        normalized = self._normalize_compatibility_options(options)
        action = normalized.get("action")

        if normalized["doctor"]:
            self._run_doctor()
            return

        if action == "service":
            self._run_service(interval=normalized["interval"], sleep=normalized["sleep"])
            return

        if normalized["enable"] and normalized["disable"]:
            raise CommandError("Choose only one of --enable or --disable.")

        node = Node.get_local()
        needs_node = any(
            normalized[key]
            for key in ("enable", "disable", "discover", "samples", "snapshot")
        )
        if needs_node and node is None:
            raise CommandError("No local node is registered for this command.")

        feature = None
        if needs_node:
            try:
                feature = NodeFeature.objects.get(slug="video-cam")
            except NodeFeature.DoesNotExist as exc:
                raise CommandError("The Video Camera node feature is not configured.") from exc

        self._maybe_enable_or_disable_feature(node=node, feature=feature, options=normalized)

        if any(normalized[key] for key in ("discover", "samples", "snapshot")):
            self._ensure_feature_enabled(node, feature, auto_enable=normalized["auto_enable"])

        if normalized["discover"]:
            created, updated = VideoDevice.refresh_from_system(node=node)
            self.stdout.write(self.style.SUCCESS(f"Detected {created} new and {updated} existing video devices."))

        should_list_devices = action in {None, "list"} or any(
            normalized[key] for key in ("discover", "enable", "disable", "samples", "snapshot", "mjpeg")
        )
        if should_list_devices:
            self._list_devices(node)

        if normalized["list_streams"]:
            self._list_streams(include_inactive=normalized["include_inactive"])

        if normalized["snapshot"]:
            self._capture_snapshot(node, normalized.get("device"))

        if normalized["mjpeg"]:
            self._capture_mjpeg(
                stream_identifier=normalized.get("stream"),
                include_inactive=normalized["include_inactive"],
            )

        samples = normalized.get("samples")
        if samples is not None:
            self._capture_samples(node=node, feature=feature, options=normalized, samples=samples)

    def _maybe_enable_or_disable_feature(
        self,
        *,
        node: Node | None,
        feature: NodeFeature | None,
        options: dict[str, object],
    ) -> None:
        """Apply explicit enable and disable feature options."""

        if options["enable"]:
            NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
            self.stdout.write(self.style.SUCCESS("Enabled the Video Camera feature for the node."))
            if node is not None and not is_feature_active_for_node(node=node, slug="video-cam"):
                self.stdout.write(self.style.WARNING("Video Camera feature not auto-detected; enabling anyway."))

        if options["disable"]:
            deleted, _ = NodeFeatureAssignment.objects.filter(node=node, feature=feature).delete()
            if deleted:
                self.stdout.write(self.style.SUCCESS("Disabled the Video Camera feature for the node."))
            else:
                self.stdout.write(self.style.WARNING("Video Camera feature was not enabled for the node."))

    def _capture_samples(
        self,
        *,
        node: Node | None,
        feature: NodeFeature | None,
        options: dict[str, object],
        samples: int,
    ) -> None:
        """Capture sample frames and emit the rendered sample output path."""

        if samples <= 0:
            raise CommandError("Samples must be a positive integer.")

        if feature is not None and not feature.is_enabled and not options["enable"]:
            self.stdout.write(self.style.WARNING("Video Camera feature is currently disabled."))

        if node is not None and not is_feature_active_for_node(node=node, slug="video-cam"):
            self.stdout.write(self.style.WARNING("Video Camera feature not auto-detected; capture may fail."))

        device = self._resolve_video_device(node, options.get("device"))
        if device is None:
            raise CommandError("No video device is available for sampling.")

        output_path = self._capture_samples_video(device, samples)
        self.stdout.write(self.style.SUCCESS(f"Sample video saved to {output_path}"))

    def _ensure_feature_enabled(
        self, node: Node | None, feature: NodeFeature | None, *, auto_enable: bool
    ) -> None:
        """Ensure the local node has the camera feature enabled for active actions."""

        if node is None or feature is None:
            return

        if feature.is_enabled:
            return

        if auto_enable:
            if not is_feature_active_for_node(node=node, slug="video-cam"):
                self.stdout.write(
                    self.style.WARNING(
                        "Video Camera feature not auto-detected; enabling anyway."
                    )
                )
            NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
            self.stdout.write(
                self.style.SUCCESS("Enabled the Video Camera feature for the node.")
            )
            return

        self.stdout.write(self.style.WARNING("Video Camera feature is currently disabled."))


    def _list_streams(self, *, include_inactive: bool) -> None:
        """List configured MJPEG streams."""

        streams = MjpegStream.objects.all()
        if not include_inactive:
            streams = streams.filter(is_active=True)
        streams = streams.select_related("video_device").order_by("name")

        self.stdout.write(f"MJPEG streams: {streams.count()}")
        for stream in streams:
            status = "active" if stream.is_active else "inactive"
            self.stdout.write(
                f"- {stream.pk} {stream.name} slug={stream.slug} "
                f"device={stream.video_device_id} {status}"
            )

    def _capture_snapshot(self, node: Node | None, device_identifier: str | None) -> None:
        """Capture a snapshot from the selected video device."""

        device = self._resolve_video_device(node, device_identifier)
        if device is None:
            raise CommandError("No video device is available for snapshot capture.")

        if node is not None and not is_feature_active_for_node(node=node, slug="video-cam"):
            self.stdout.write(
                self.style.WARNING(
                    "Video Camera feature not auto-detected; snapshot may fail."
                )
            )

        try:
            snapshot = device.capture_snapshot(link_duplicates=True)
        except Exception as exc:  # pragma: no cover - hardware interaction
            raise CommandError(str(exc)) from exc

        if not snapshot:
            self.stdout.write(self.style.WARNING("Duplicate snapshot; not saved."))
            return

        self.stdout.write(self.style.SUCCESS(f"Snapshot saved to {snapshot.sample.path}"))

    def _capture_mjpeg(
        self,
        *,
        stream_identifier: str | None,
        include_inactive: bool,
    ) -> None:
        """Copy cached MJPEG frames into persistent snapshot records."""

        streams = self._resolve_streams(stream_identifier, include_inactive)
        if not streams:
            if stream_identifier:
                raise CommandError("No MJPEG stream matched the requested identifier.")
            self.stdout.write(self.style.WARNING("No MJPEG streams found to capture."))
            return

        captured = 0
        skipped = 0
        failed = 0
        for stream in streams:
            cached = get_frame(stream)
            if not cached:
                skipped += 1
                continue
            try:
                stream.store_frame_bytes(cached.frame_bytes, update_thumbnail=True)
            except Exception:  # pragma: no cover - best-effort diagnostics
                failed += 1
                continue
            captured += 1

        if captured:
            self.stdout.write(self.style.SUCCESS(f"Captured frames for {captured} stream(s)."))
        if skipped:
            self.stdout.write(
                self.style.WARNING(f"Skipped {skipped} stream(s) without frames.")
            )
        if failed:
            self.stdout.write(
                self.style.WARNING(f"Failed to capture frames for {failed} stream(s).")
            )

    def _resolve_streams(
        self, stream_identifier: str | None, include_inactive: bool
    ) -> list[MjpegStream]:
        """Resolve one or many MJPEG streams from the provided selector."""

        queryset = MjpegStream.objects.all()
        if not include_inactive:
            queryset = queryset.filter(is_active=True)

        if not stream_identifier:
            return list(queryset.order_by("name"))

        if stream_identifier.isdigit():
            stream = queryset.filter(pk=int(stream_identifier)).first()
            if stream:
                return [stream]

        stream = queryset.filter(slug=stream_identifier).first()
        return [stream] if stream else []

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

        feature_available = bool(
            node and is_feature_active_for_node(node=node, slug="video-cam")
        )
        feature_status = "available" if feature_available else "missing"
        self.stdout.write(f"Video feature detection: {feature_status}")
        camera_probe = probe_rpi_camera_stack()
        camera_stack = "available" if camera_probe.available else "missing"
        probe_detail = (
            f"{camera_probe.backend}: {camera_probe.reason}"
            if camera_probe.available
            else camera_probe.reason
        )
        self.stdout.write(f"Camera stack probe: {camera_stack} ({probe_detail})")

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

        counts = MjpegStream.objects.aggregate(
            total=Count("pk"),
            active=Count("pk", filter=Q(is_active=True)),
        )
        self.stdout.write(
            f"MJPEG streams: {counts['active']} active / {counts['total']} total"
        )

    def _report_frame_cache_status(self) -> None:
        """Report Redis-backed frame cache connectivity and sample data."""

        if not frame_cache_url():
            self.stdout.write(
                self.style.WARNING(
                    "Frame cache: Redis URL is not configured."
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


    def _run_service(self, *, interval: float, sleep: float) -> None:
        """Run the MJPEG camera capture service loop."""

        if not frame_cache_url():
            raise CommandError("A Redis URL must be configured to use camera_service.")

        captures: dict[int, _StreamCapture] = {}
        self.stdout.write(self.style.SUCCESS("Starting camera service..."))
        try:
            while True:
                streams = (
                    MjpegStream.objects.filter(is_active=True)
                    .select_related("video_device")
                    .order_by("pk")
                )
                active_ids = {stream.pk for stream in streams}
                for stream_id in list(captures):
                    if stream_id not in active_ids:
                        captures[stream_id].close()
                        captures.pop(stream_id, None)

                for stream in streams:
                    capture = captures.get(stream.pk)
                    if capture is None:
                        capture = _StreamCapture(stream)
                        captures[stream.pk] = capture
                    try:
                        frame_bytes = capture.capture_frame(interval=interval)
                    except MjpegDependencyError as exc:
                        capture._last_error = str(exc)
                        logger.warning("MJPEG dependency error for %s: %s", stream.slug, exc)
                        store_status(stream, capture.status_payload())
                        capture.log_status()
                        continue
                    except Exception as exc:  # pragma: no cover - runtime device error
                        capture._last_error = str(exc)
                        logger.warning("Camera capture error for %s: %s", stream.slug, exc)
                        store_status(stream, capture.status_payload())
                        capture.log_status()
                        continue

                    payload = capture.status_payload()
                    if frame_bytes:
                        store_frame(stream, frame_bytes)
                    if frame_bytes or payload.get("last_error"):
                        store_status(stream, payload)
                        capture.log_status()
                time.sleep(sleep)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Camera service stopped."))
        finally:
            for capture in captures.values():
                capture.close()

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
        """Capture sample frames and assemble them into a short video file.

        Prefer cached MJPEG frames from Redis when the camera service is active for the
        selected device. Fall back to direct device snapshots only when the camera
        service is not active.
        """

        frames_dir, output_path = self._get_video_paths()
        frames_dir.mkdir(parents=True, exist_ok=True)

        if self._capture_samples_from_camera_service(device, samples, frames_dir):
            self._encode_video(frames_dir, output_path)
            return output_path

        for index in range(1, samples + 1):
            snapshot_path = device.capture_snapshot_path()
            target_path = frames_dir / f"frame-{index:03d}.jpg"
            shutil.copy2(snapshot_path, target_path)

        self._encode_video(frames_dir, output_path)
        return output_path

    def _capture_samples_from_camera_service(
        self,
        device: VideoDevice,
        samples: int,
        frames_dir: Path,
    ) -> bool:
        """Capture sample frames from the Redis-backed camera service when active.

        Returns ``True`` when frames were captured from Redis. Returns ``False`` when the
        camera service is not configured or not active for ``device``.
        """

        if not frame_cache_url():
            return False

        stream = (
            MjpegStream.objects.filter(video_device=device, is_active=True)
            .order_by("pk")
            .first()
        )
        if stream is None:
            return False

        status_payload = get_status(stream) or {}
        status_is_fresh = self._camera_service_status_is_fresh(status_payload)
        cached_frame = get_frame(stream)
        service_is_active = status_is_fresh or cached_frame is not None
        if not service_is_active:
            return False

        if cached_frame is None:
            return False

        first_path = frames_dir / "frame-001.jpg"
        first_path.write_bytes(cached_frame.frame_bytes)
        last_frame_id = cached_frame.frame_id

        for index in range(2, samples + 1):
            next_frame = self._wait_for_next_camera_service_frame(
                stream=stream,
                last_frame_id=last_frame_id,
                last_frame_bytes=cached_frame.frame_bytes,
            )
            if next_frame is None:
                raise CommandError(
                    f"Timed out waiting for a new cached frame while collecting samples for stream '{stream.slug}'."
                )
            last_frame_id = next_frame.frame_id
            cached_frame = next_frame
            target_path = frames_dir / f"frame-{index:03d}.jpg"
            target_path.write_bytes(next_frame.frame_bytes)

        self.stdout.write(
            self.style.SUCCESS(
                f"Captured {samples} sample frame(s) from camera service stream '{stream.slug}'."
            )
        )
        return True

    def _wait_for_next_camera_service_frame(
        self,
        *,
        stream: MjpegStream,
        last_frame_id: int | None,
        last_frame_bytes: bytes | None,
    ) -> CachedFrame | None:
        """Poll Redis until a newer cached frame arrives for ``stream``.

        When ``last_frame_id`` is unknown, compare frame bytes to avoid duplicates.
        """

        deadline = time.monotonic() + self._CAMERA_SERVICE_FRAME_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            cached = get_frame(stream)
            if cached and cached.frame_bytes:
                if cached.frame_id is not None and last_frame_id is not None:
                    if cached.frame_id != last_frame_id:
                        return cached
                elif cached.frame_bytes != last_frame_bytes:
                    return cached
            time.sleep(self._CAMERA_SERVICE_FRAME_POLL_SECONDS)
        return None

    def _camera_service_status_is_fresh(self, status_payload: dict[str, object]) -> bool:
        """Return ``True`` when status metadata suggests the service is currently active."""

        if not status_payload:
            return False

        updated_at_raw = status_payload.get("updated_at")
        if not isinstance(updated_at_raw, str):
            return False

        updated_at = parse_datetime(updated_at_raw)
        if updated_at is None:
            return False
        if django_timezone.is_naive(updated_at):
            updated_at = django_timezone.make_aware(updated_at, timezone=timezone.utc)

        age_seconds = (django_timezone.now() - updated_at).total_seconds()
        return age_seconds <= self._CAMERA_SERVICE_STATUS_STALE_SECONDS

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
