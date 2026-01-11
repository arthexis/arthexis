from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.content.utils import save_screenshot
from apps.video.models import VideoDevice
from apps.video.utils import capture_rpi_snapshot, has_rpi_camera_stack


class Command(BaseCommand):
    """Capture a camera snapshot and record it as a content sample."""

    help = "Capture a snapshot from the default camera and print the file path."

    def handle(self, *args, **options) -> str:
        if not _is_test_server_active():
            raise CommandError(
                "Snapshot capture is only available when running in the Test Server."
            )

        node = Node.get_local()
        if node is None:
            raise CommandError("No local node is registered; cannot take a snapshot.")

        try:
            feature = NodeFeature.objects.get(slug="rpi-camera")
        except NodeFeature.DoesNotExist:
            raise CommandError("The rpi-camera node feature is not configured.")

        if not feature.is_enabled:
            if not has_rpi_camera_stack():
                self.stdout.write(
                    self.style.WARNING(
                        "Raspberry Pi camera stack not detected; attempting to enable the feature anyway."
                    )
                )
            NodeFeatureAssignment.objects.update_or_create(node=node, feature=feature)
            self.stdout.write(
                self.style.SUCCESS("Enabled the rpi-camera feature for the local node.")
            )

        if not VideoDevice.objects.filter(node=node).exists():
            created, updated = VideoDevice.refresh_from_system(node=node)
            if created or updated:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Detected {created} new and {updated} existing video devices."
                    )
                )

        if not VideoDevice.objects.filter(node=node).exists():
            raise CommandError("No video devices were detected on this node.")

        try:
            path = capture_rpi_snapshot()
        except Exception as exc:  # pragma: no cover - depends on camera stack
            raise CommandError(str(exc)) from exc

        sample = save_screenshot(
            path, node=node, method="RPI_CAMERA", link_duplicates=True
        )
        if not sample:
            self.stdout.write(self.style.WARNING("Duplicate snapshot; not saved."))
            self.stdout.write(str(path))
            return str(path)

        saved_path = Path(sample.path).as_posix()
        self.stdout.write(self.style.SUCCESS(f"Snapshot saved to {saved_path}"))
        return saved_path


def _is_test_server_active() -> bool:
    """Return ``True`` when the VS Code test server is running outside CI."""

    if os.environ.get("CI"):
        return False
    lock_file = Path(settings.BASE_DIR) / ".locks" / "test_server.json"
    return lock_file.exists()
