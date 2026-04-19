from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from django.core.management import call_command


def test_classify_camera_streams_continues_after_stream_failure(monkeypatch):
    first_stream = SimpleNamespace(slug="first-stream")
    second_stream = SimpleNamespace(slug="second-stream")

    def _resolve_classifier(_self, _value):
        return None

    def _resolve_streams(_self, _value):
        return [first_stream, second_stream]

    def _classify_stream(stream, *, classifier=None):
        if stream.slug == "first-stream":
            raise RuntimeError("camera unavailable")
        return object(), [object(), object()]

    monkeypatch.setattr(
        "apps.classification.management.commands.classify_camera_streams.Command._resolve_classifier",
        _resolve_classifier,
    )
    monkeypatch.setattr(
        "apps.classification.management.commands.classify_camera_streams.Command._resolve_streams",
        _resolve_streams,
    )
    monkeypatch.setattr(
        "apps.classification.management.commands.classify_camera_streams.classify_stream",
        _classify_stream,
    )

    stdout = StringIO()
    stderr = StringIO()
    call_command("classify_camera_streams", stdout=stdout, stderr=stderr)

    assert "first-stream: classification failed" in stderr.getvalue()
    assert "second-stream: created 2 classification record(s)." in stdout.getvalue()
    assert "failed 1 stream(s)." in stdout.getvalue()
