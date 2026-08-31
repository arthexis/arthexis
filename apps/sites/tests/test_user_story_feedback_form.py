from types import SimpleNamespace

import pytest
from django.db import DatabaseError
from django.urls import reverse

from apps.sites.forms import UserStoryForm
from apps.sites.models import UserStory
from apps.sites.views import landing

pytestmark = [pytest.mark.django_db]


def test_user_story_form_persists_javascript_enabled_as_true():
    form = UserStoryForm(
        data={
            "name": "feedback@example.com",
            "rating": 4,
            "comments": "Needs a few improvements.",
            "path": "/admin/",
            "messages": "",
        }
    )

    assert form.is_valid(), form.errors

    story = form.save()

    assert story.javascript_enabled is True


def test_user_story_form_appends_feedback_context_to_messages():
    form = UserStoryForm(
        data={
            "name": "feedback@example.com",
            "rating": 4,
            "comments": "The selected card needs a clearer preview.",
            "path": "/cards/command-templates/example/",
            "messages": "Existing page message",
            "feedback_context": "Image ID: 42 | Image UUID: abc",
        }
    )

    assert form.is_valid(), form.errors

    story = form.save()

    assert (
        story.messages
        == "Existing page message | Context: Image ID: 42 | Image UUID: abc"
    )


def test_user_story_form_ignores_unavailable_chat_profile_storage():
    class UserWithBrokenChatProfile:
        is_authenticated = True
        is_staff = False

        def get_username(self):
            return "feedback-user"

        def get_profile(self, profile_cls):
            raise DatabaseError("no such table: chats_chatavatar")

    form = UserStoryForm(
        data={
            "name": "feedback@example.com",
            "rating": 4,
            "comments": "Needs a few improvements.",
            "path": "/admin/",
            "messages": "",
        },
        user=UserWithBrokenChatProfile(),
    )

    assert form.fields["contact_via_chat"].initial is False


def test_authenticated_user_can_submit_public_site_feedback(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="feedback-user",
        password="unused-password",
    )
    client.force_login(user)

    response = client.post(
        reverse("pages:user-story-submit"),
        {
            "rating": 4,
            "comments": "The charging-session list is clear.",
            "path": "/ocpp/",
            "messages": "",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}
    story = UserStory.objects.get()
    assert story.owner == user
    assert story.user == user
    assert story.name == user.username


def test_superuser_feedback_throttle_uses_shorter_cooldown(rf, monkeypatch, settings):
    settings.USER_STORY_THROTTLE_SECONDS = 300
    cache_calls = []

    def fake_cache_add(key, value, timeout):
        cache_calls.append((key, timeout))
        return False

    monkeypatch.setattr(
        landing, "is_suite_feature_enabled", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(landing.cache, "add", fake_cache_add)
    request = rf.post("/feedback/user-story/")
    request.user = SimpleNamespace(is_authenticated=True, is_superuser=True, pk=1)

    response = landing.submit_user_story(request)

    assert response.status_code == 429
    assert cache_calls == [("user-story:superuser:1", 30)]
    assert b"30 seconds" in response.content


def test_staff_feedback_throttle_uses_two_minute_cooldown(rf, monkeypatch, settings):
    settings.USER_STORY_THROTTLE_SECONDS = 300
    cache_calls = []

    def fake_cache_add(key, value, timeout):
        cache_calls.append((key, timeout))
        return False

    monkeypatch.setattr(
        landing, "is_suite_feature_enabled", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(landing.cache, "add", fake_cache_add)
    request = rf.post("/feedback/user-story/")
    request.user = SimpleNamespace(
        is_authenticated=True,
        is_staff=True,
        is_superuser=False,
        pk=7,
    )

    response = landing.submit_user_story(request)

    assert response.status_code == 429
    assert cache_calls == [("user-story:staff:7", 120)]
    assert b"2 minutes" in response.content


def test_regular_feedback_throttle_keeps_default_cooldown(rf, monkeypatch, settings):
    settings.USER_STORY_THROTTLE_SECONDS = 300
    cache_calls = []

    def fake_cache_add(key, value, timeout):
        cache_calls.append((key, timeout))
        return False

    monkeypatch.setattr(
        landing, "is_suite_feature_enabled", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(landing.cache, "add", fake_cache_add)
    request = rf.post("/feedback/user-story/")
    request.user = SimpleNamespace(is_authenticated=False, is_superuser=False)

    response = landing.submit_user_story(request)

    assert response.status_code == 429
    assert cache_calls == [("user-story:ip:127.0.0.1", 300)]
    assert b"5 minutes" in response.content


def test_feedback_throttle_error_uses_singular_seconds():
    assert (
        landing._format_user_story_throttle_error(1)
        == "You can only submit feedback once every 1 second."
    )


def test_feedback_throttle_error_uses_singular_minutes():
    assert (
        landing._format_user_story_throttle_error(60)
        == "You can only submit feedback once every 1 minute."
    )


def _feedback_payload(path="/admin/pages/userstory/"):
    return {
        "name": "feedback@example.com",
        "rating": 4,
        "comments": "Please keep context with the issue.",
        "path": path,
        "messages": "",
    }


def test_staff_feedback_does_not_capture_screenshot(rf, settings, tmp_path):
    settings.USER_STORY_THROTTLE_SECONDS = 0
    settings.MEDIA_ROOT = tmp_path / "media"
    request = rf.post("/feedback/user-story/", data=_feedback_payload())
    request.user = SimpleNamespace(
        is_authenticated=False, is_staff=True, is_superuser=False
    )
    request.session = {}
    request.COOKIES["sessionid"] = "session-cookie"

    response = landing.submit_user_story(request)

    story = UserStory.objects.get()
    assert response.status_code == 200
    assert not story.screenshot


def test_public_feedback_does_not_capture_screenshot(
    rf, settings, tmp_path
):
    settings.USER_STORY_THROTTLE_SECONDS = 0
    settings.MEDIA_ROOT = tmp_path / "media"
    request = rf.post("/feedback/user-story/", data=_feedback_payload(path="/"))
    request.user = SimpleNamespace(
        is_authenticated=False, is_staff=False, is_superuser=False
    )
    request.session = {}

    response = landing.submit_user_story(request)

    story = UserStory.objects.get()
    assert response.status_code == 200
    assert not story.screenshot


def test_public_feedback_does_not_capture_screenshot_with_internal_path(
    rf, settings, tmp_path
):
    settings.USER_STORY_THROTTLE_SECONDS = 0
    settings.MEDIA_ROOT = tmp_path / "media"
    request = rf.post(
        "/feedback/user-story/",
        data=_feedback_payload(path="/internal/status"),
    )
    request.user = SimpleNamespace(
        is_authenticated=False, is_staff=False, is_superuser=False
    )
    request.session = {}

    response = landing.submit_user_story(request)

    story = UserStory.objects.get()
    assert response.status_code == 200
    assert not story.screenshot
    assert not story.allow_feedback_issue_label_tags


def test_public_feedback_does_not_allow_issue_label_tags(
    rf, settings, tmp_path
):
    settings.USER_STORY_THROTTLE_SECONDS = 0
    settings.MEDIA_ROOT = tmp_path / "media"
    request = rf.post(
        "/feedback/user-story/",
        data=_feedback_payload(path="/admin/"),
    )
    request.user = SimpleNamespace(
        is_authenticated=False, is_staff=False, is_superuser=False
    )
    request.session = {}

    response = landing.submit_user_story(request)

    story = UserStory.objects.get()
    assert response.status_code == 200
    assert not story.screenshot
    assert not story.allow_feedback_issue_label_tags


def test_superuser_feedback_allows_issue_label_tags(rf, monkeypatch, settings):
    settings.USER_STORY_THROTTLE_SECONDS = 0
    monkeypatch.setattr(
        landing, "is_suite_feature_enabled", lambda *args, **kwargs: True
    )
    request = rf.post(
        "/feedback/user-story/",
        data=_feedback_payload(path="/admin/"),
    )
    request.user = SimpleNamespace(
        is_authenticated=False, is_staff=False, is_superuser=True
    )
    request.session = {}

    response = landing.submit_user_story(request)

    story = UserStory.objects.get()
    assert response.status_code == 200
    assert story.allow_feedback_issue_label_tags
