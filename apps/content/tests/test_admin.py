"""Tests for content admin feature-gated camera snapshot actions."""

from __future__ import annotations

from unittest.mock import ANY, Mock

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.content.models import ContentSample
from apps.nodes.models import Node, NodeFeature


@pytest.mark.django_db
def test_take_snapshot_rejects_get_requests(admin_client):
    """Snapshot action must be POST-only to avoid unsafe state changes on GET."""

    Node._local_cache.clear()
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    sample = ContentSample.objects.create(kind=ContentSample.IMAGE, path="snap.jpg", node=node)

    response = admin_client.get(
        reverse("admin:content_contentsample_take_snapshot", args=[sample.pk])
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_take_snapshot_requires_video_feature(admin_client):
    """The content snapshot action should refuse requests when the video feature is disabled."""

    Node._local_cache.clear()
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    NodeFeature.objects.create(slug="video-cam", display="Video Camera")
    sample = ContentSample.objects.create(kind=ContentSample.IMAGE, path="snap.jpg", node=node)

    response = admin_client.post(
        reverse("admin:content_contentsample_take_snapshot", args=[sample.pk]),
        follow=True,
    )

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("Video Camera feature is not enabled on this node." in msg for msg in messages)
    assert response.status_code == 200


@pytest.mark.django_db
def test_take_snapshot_uses_resolved_feature_state(admin_client, monkeypatch, tmp_path):
    """The content snapshot action should proceed when feature resolution reports enabled."""

    Node._local_cache.clear()
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    NodeFeature.objects.create(slug="video-cam", display="Video Camera")

    sample = ContentSample.objects.create(kind=ContentSample.IMAGE, path="snap.jpg", node=node)

    snapshot_path = tmp_path / "snapshot.jpg"
    snapshot_path.write_bytes(b"snapshot")

    monkeypatch.setattr("apps.content.admin.capture_rpi_snapshot", lambda **_kwargs: snapshot_path)
    monkeypatch.setattr(NodeFeature, "is_enabled", property(lambda self: True))

    new_sample = ContentSample(pk=sample.pk + 1)
    save_screenshot_mock = Mock(return_value=new_sample)
    monkeypatch.setattr("apps.content.admin.save_screenshot", save_screenshot_mock)

    response = admin_client.post(
        reverse("admin:content_contentsample_take_snapshot", args=[sample.pk])
    )

    assert response.status_code == 302
    save_screenshot_mock.assert_called_once_with(
        snapshot_path,
        node=node,
        method="RPI_CAMERA",
        user=ANY,
        link_duplicates=True,
    )
