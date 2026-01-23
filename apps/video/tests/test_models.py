import re

import pytest

from apps.nodes.models import Node
from apps.video.models import VideoDevice


@pytest.fixture
def node(db):
    return Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )


@pytest.mark.django_db
def test_videodevice_generates_slug_when_missing(node):
    device = VideoDevice.objects.create(
        node=node,
        identifier="/dev/video-test",
        name="Lobby Cam",
        slug="",
    )

    assert device.slug == "lobby-cam"


@pytest.mark.django_db
def test_videodevice_preserves_existing_slug(node):
    device = VideoDevice.objects.create(
        node=node,
        identifier="/dev/video-test",
        name="Lobby Cam",
        slug="custom-slug",
    )

    device.name = "Lobby Cam Updated"
    device.save()

    assert device.slug == "custom-slug"


@pytest.mark.django_db
def test_videodevice_default_name_when_empty(node):
    device = VideoDevice.objects.create(
        node=node,
        identifier="/dev/video-test",
        name="   ",
        slug="",
    )

    assert device.name == VideoDevice.DEFAULT_NAME
    assert device.slug == "base-migrate"


@pytest.mark.django_db
def test_videodevice_slug_fallback_when_slugify_empty(node):
    device = VideoDevice.objects.create(
        node=node,
        identifier="/dev/video-test",
        name="!!!",
        slug="",
    )

    assert device.slug
    assert re.fullmatch(r"[0-9a-f]{12}", device.slug)


@pytest.mark.django_db
def test_videodevice_display_name_priority(node):
    device = VideoDevice(
        node=node,
        identifier="device-id",
        name="Camera One",
        slug="camera-one",
    )

    assert device.display_name == "Camera One"

    device.name = "  "
    assert device.display_name == "camera-one"

    device.slug = "   "
    assert device.display_name == "device-id"
