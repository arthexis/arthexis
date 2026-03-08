from __future__ import annotations

from apps.content.video import rfid


def test_is_video_camera_feature_active_uses_local_feature_helper(monkeypatch):
    """Video RFID helpers should defer to local node feature gating."""

    monkeypatch.setattr(rfid, "is_local_node_feature_active", lambda slug: slug == "video-cam")

    assert rfid.is_video_camera_feature_active() is True
