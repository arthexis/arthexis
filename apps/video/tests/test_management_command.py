from __future__ import annotations

import io

from apps.video.management.commands import video as video_command
from apps.video.utils import CameraStackProbe


def test_run_doctor_reports_camera_probe_reason(monkeypatch):
    """Video doctor should include the camera probe reason in its output."""

    command = video_command.Command()
    command.stdout = io.StringIO()

    monkeypatch.setattr(video_command.Node, "get_local", staticmethod(lambda: None))

    class _EmptyQuerySet:
        @staticmethod
        def first():
            return None

    class _EmptyManager:
        @staticmethod
        def filter(**_kwargs):
            return _EmptyQuerySet()

    monkeypatch.setattr(
        video_command,
        "NodeFeature",
        type("NodeFeatureStub", (), {"objects": _EmptyManager()}),
    )
    monkeypatch.setattr(
        video_command,
        "probe_rpi_camera_stack",
        lambda: CameraStackProbe(
            available=False,
            backend="missing",
            reason="No attached cameras detected",
        ),
    )
    monkeypatch.setattr(command, "_report_devices", lambda node: None)
    monkeypatch.setattr(command, "_report_streams", lambda: None)
    monkeypatch.setattr(command, "_report_frame_cache_status", lambda: None)

    command._run_doctor()

    output = command.stdout.getvalue()
    assert "Camera stack probe: missing (No attached cameras detected)" in output
