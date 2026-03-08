import pytest

from apps.nodes.models import Node
from apps.content.video.models import VideoDevice


@pytest.mark.django_db
def test_refresh_from_system_skips_when_video_feature_inactive(monkeypatch):
    """Video sync should not probe devices when video-cam is inactive."""

    node = Node.objects.create(
        hostname="local-video-gated",
        current_relation=Node.Relation.SELF,
        mac_address=Node.get_current_mac(),
    )

    monkeypatch.setattr(
        "apps.content.video.models.device.is_feature_active_for_node",
        lambda *, node, slug: False,
    )

    def _detect_devices() -> list[object]:
        raise AssertionError("device detection should not run when feature is inactive")

    monkeypatch.setattr(VideoDevice, "detect_devices", classmethod(lambda cls: _detect_devices()))

    created, updated = VideoDevice.refresh_from_system(node=node)

    assert (created, updated) == (0, 0)
