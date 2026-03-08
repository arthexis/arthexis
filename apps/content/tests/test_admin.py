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


