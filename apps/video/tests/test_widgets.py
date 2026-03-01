from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment
from apps.video.models import MjpegStream, VideoDevice
from apps.video.widgets import camera_sidebar_widget


@pytest.mark.django_db
@pytest.mark.regression
def test_camera_sidebar_widget_returns_stream_link_when_feature_enabled(monkeypatch):
    """Camera widget should expose a stream link for the default camera."""

    user = get_user_model().objects.create_user(username="cam-admin", is_staff=True)
    request = RequestFactory().get("/admin/")
    request.user = user

    node = Node.objects.create(hostname="camera-node", current_relation=Node.Relation.SELF)
    monkeypatch.setattr(node, "sync_feature_tasks", lambda: None)
    request.badge_node = node

    feature = NodeFeature.objects.create(slug="video-cam", display="Video Camera")
    NodeFeatureAssignment.objects.create(node=node, feature=feature)

    device = VideoDevice.objects.create(node=node, identifier="/dev/video0", is_default=True)
    stream = MjpegStream.objects.create(name="Lobby", slug="lobby", video_device=device)

    context = camera_sidebar_widget(request=request)

    assert context is not None
    assert context["stream"] == stream
    assert context["stream_url"].endswith(f"/{stream.slug}/")
