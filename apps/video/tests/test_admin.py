"""Admin tests for video app."""

import pytest
import requests
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from apps.video import admin as video_admin
from apps.video.models import YoutubeChannel


@pytest.fixture
def admin_user(db):
    """Create an admin user for admin action tests."""
    user_model = get_user_model()
    return user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password",
    )


def build_admin_request(factory, user):
    """Build an admin POST request with session and message storage configured."""
    request = factory.post("/admin/")
    request.user = user
    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()
    messages_storage = FallbackStorage(request)
    setattr(request, "_messages", messages_storage)
    return request


@pytest.mark.django_db
def test_youtube_channel_action_reports_failure(monkeypatch, admin_user):
    """YouTube test action should emit failure messages when request probing fails."""
    channel = YoutubeChannel.objects.create(
        title="Arthexis",
        channel_id="UC9999fail",
    )
    request = build_admin_request(RequestFactory(), admin_user)
    admin_view = video_admin.YoutubeChannelAdmin(YoutubeChannel, admin.site)

    def fake_get(url, timeout):
        raise requests.RequestException("network down")

    monkeypatch.setattr(video_admin.requests, "get", fake_get)

    admin_view.test_selected_channel(
        request,
        YoutubeChannel.objects.filter(pk=channel.pk),
    )

    messages = [str(message) for message in request._messages]
    assert any("Failed to reach" in message for message in messages)
    assert any("Failed to test 1 channel" in message for message in messages)
