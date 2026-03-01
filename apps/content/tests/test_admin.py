"""Tests for content admin feature-gated camera snapshot actions."""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.content.models import ContentSample
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment


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

    response = admin_client.get(
        reverse("admin:content_contentsample_take_snapshot", args=[sample.pk]),
        follow=True,
    )

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("Video Camera feature is not enabled on this node." in msg for msg in messages)
    assert response.status_code == 200


@pytest.mark.django_db
def test_take_snapshot_uses_feature_state_for_enabled_node(admin_client, monkeypatch, tmp_path):
    """The content snapshot action should proceed when the node feature is enabled."""

    Node._local_cache.clear()
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    feature = NodeFeature.objects.create(slug="video-cam", display="Video Camera")
    NodeFeatureAssignment.objects.create(node=node, feature=feature)

    sample = ContentSample.objects.create(kind=ContentSample.IMAGE, path="snap.jpg", node=node)

    snapshot_path = tmp_path / "snapshot.jpg"
    snapshot_path.write_bytes(b"snapshot")

    monkeypatch.setattr("apps.content.admin.capture_rpi_snapshot", lambda **_kwargs: snapshot_path)

    saved = {"called": False}

    def _fake_save_screenshot(path, **kwargs):
        saved["called"] = True
        return ContentSample.objects.create(kind=ContentSample.IMAGE, path=str(path), node=node)

    monkeypatch.setattr("apps.content.admin.save_screenshot", _fake_save_screenshot)

    response = admin_client.get(
        reverse("admin:content_contentsample_take_snapshot", args=[sample.pk])
    )

    assert response.status_code == 302
    assert saved["called"] is True
