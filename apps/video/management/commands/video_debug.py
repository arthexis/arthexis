from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.video.models import (
    MjpegDependencyError,
    MjpegDeviceUnavailableError,
    MjpegStream,
    VideoDevice,
)
from apps.video.utils import has_rpi_camera_stack


class Command(BaseCommand):
    """Debug video snapshots and MJPEG streams."""

    help = "Run snapshot and MJPEG stream diagnostics."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--list",
            action="store_true",
            help="List video devices and MJPEG streams.",
        )
        parser.add_argument(
            "--snapshot",
            action="store_true",
            help="Capture a snapshot from a video device.",
        )
        parser.add_argument(
            "--device",
            help="Video device ID, slug, or identifier to use for snapshot capture.",
        )
        parser.add_argument(
            "--refresh-devices",
            action="store_true",
            help="Refresh video devices before capturing snapshots.",
        )
        parser.add_argument(
            "--auto-enable",
            action="store_true",
            help="Enable the rpi-camera feature if it is disabled.",
        )
        parser.add_argument(
            "--mjpeg",
            action="store_true",
            help="Capture a frame from MJPEG streams.",
        )
        parser.add_argument(
            "--stream",
            help="MJPEG stream slug or ID to capture.",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="Include inactive MJPEG streams.",
        )

    def handle(self, *args, **options) -> None:
        if not any(options[key] for key in ("list", "snapshot", "mjpeg")):
            options["list"] = True

        node = Node.get_local()
        self._print_node_summary(node)

        if options["list"]:
            self._list_devices(node)
            self._list_streams(include_inactive=options["include_inactive"])

        if options["snapshot"]:
            self._capture_snapshot(
                node,
                device_identifier=options.get("device"),
                refresh_devices=options["refresh_devices"],
                auto_enable=options["auto_enable"],
            )

        if options["mjpeg"]:
            self._capture_mjpeg(
                stream_identifier=options.get("stream"),
                include_inactive=options["include_inactive"],
            )

    def _print_node_summary(self, node: Node | None) -> None:
        if node is None:
            self.stdout.write(
                self.style.WARNING(
                    "Local node is not registered; some checks may be unavailable."
                )
            )
            return
        self.stdout.write(
            self.style.SUCCESS(f"Local node: {node.hostname} (id={node.pk})")
        )

    def _list_devices(self, node: Node | None) -> None:
        if node:
            devices = VideoDevice.objects.filter(node=node).order_by("identifier")
        else:
            devices = VideoDevice.objects.all().order_by("identifier")

        self.stdout.write(f"Video devices: {devices.count()}")
        for device in devices:
            self.stdout.write(
                f"- {device.pk} {device.display_name} "
                f"identifier={device.identifier} node={device.node_id}"
            )

    def _list_streams(self, *, include_inactive: bool) -> None:
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

    def _capture_snapshot(
        self,
        node: Node | None,
        *,
        device_identifier: str | None,
        refresh_devices: bool,
        auto_enable: bool,
    ) -> None:
        if node is None:
            raise CommandError("No local node is registered; cannot capture snapshots.")

        try:
            feature = NodeFeature.objects.get(slug="rpi-camera")
        except NodeFeature.DoesNotExist as exc:
            raise CommandError("The rpi-camera node feature is not configured.") from exc

        if not feature.is_enabled:
            if auto_enable:
                if not has_rpi_camera_stack():
                    self.stdout.write(
                        self.style.WARNING(
                            "Raspberry Pi camera stack not detected; enabling anyway."
                        )
                    )
                NodeFeatureAssignment.objects.update_or_create(
                    node=node, feature=feature
                )
                self.stdout.write(
                    self.style.SUCCESS("Enabled the rpi-camera feature for the node.")
                )
            else:
                self.stdout.write(
                    self.style.WARNING("rpi-camera feature is currently disabled.")
                )

        if refresh_devices:
            created, updated = VideoDevice.refresh_from_system(node=node)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Detected {created} new and {updated} existing video devices."
                )
            )

        device = self._resolve_video_device(node, device_identifier)
        if device is None:
            raise CommandError("No video device is available for snapshot capture.")

        if not has_rpi_camera_stack():
            self.stdout.write(
                self.style.WARNING(
                    "Raspberry Pi camera stack not detected; snapshot may fail."
                )
            )

        try:
            snapshot = device.capture_snapshot(link_duplicates=True)
        except Exception as exc:  # pragma: no cover - hardware interaction
            raise CommandError(str(exc)) from exc

        if not snapshot:
            self.stdout.write(self.style.WARNING("Duplicate snapshot; not saved."))
            return

        self.stdout.write(
            self.style.SUCCESS(f"Snapshot saved to {snapshot.sample.path}")
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

        device = None
        if device_identifier.isdigit():
            device = queryset.filter(pk=int(device_identifier)).first()
        if device:
            return device
        device = queryset.filter(slug=device_identifier).first()
        if device:
            return device
        return queryset.filter(identifier=device_identifier).first()

    def _capture_mjpeg(
        self,
        *,
        stream_identifier: str | None,
        include_inactive: bool,
    ) -> None:
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
            try:
                frame_bytes = stream.capture_frame_bytes()
            except (MjpegDependencyError, MjpegDeviceUnavailableError, RuntimeError) as exc:
                if isinstance(exc, MjpegDependencyError) or self._is_missing_mjpeg_dependency(exc):
                    failed += 1
                else:
                    failed += 1
                continue
            except Exception:  # pragma: no cover - best-effort diagnostics
                failed += 1
                continue

            if not frame_bytes:
                skipped += 1
                continue

            try:
                stream.store_frame_bytes(frame_bytes, update_thumbnail=True)
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
                self.style.WARNING(
                    f"Failed to capture frames for {failed} stream(s)."
                )
            )

    def _resolve_streams(
        self, stream_identifier: str | None, include_inactive: bool
    ) -> list[MjpegStream]:
        queryset = MjpegStream.objects.all()
        if not include_inactive:
            queryset = queryset.filter(is_active=True)

        if not stream_identifier:
            return list(queryset.order_by("name"))

        if stream_identifier.isdigit():
            stream = queryset.filter(pk=int(stream_identifier)).first()
            return [stream] if stream else []

        stream = queryset.filter(slug=stream_identifier).first()
        return [stream] if stream else []

    @staticmethod
    def _is_missing_mjpeg_dependency(exc: Exception) -> bool:
        return "OpenCV (cv2)" in str(exc)
