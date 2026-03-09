"""Regression tests for user story feedback submission rules."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils.datastructures import MultiValueDict


from apps.sites.forms import UserStoryForm
from apps.sites.models import UserStory, UserStoryAttachment
from apps.features.models import Feature

pytestmark = [pytest.mark.django_db]


def test_anonymous_user_cannot_upload_feedback_files(client, settings):
    """Anonymous feedback should reject uploaded files."""

    settings.USER_STORY_THROTTLE_SECONDS = 0

    response = client.post(
        reverse("pages:user-story-submit"),
        data={
            "rating": 4,
            "comments": "Anon feedback",
            "path": "/",
            "attachments": SimpleUploadedFile("anon.txt", b"anon"),
        },
    )

    assert response.status_code == 400
    assert "attachments" in response.json()["errors"]


def test_submission_rejected_when_feedback_ingestion_feature_disabled(client, settings):
    """Regression: submissions should be rejected when feedback ingestion is disabled."""

    settings.USER_STORY_THROTTLE_SECONDS = 0
    Feature.objects.update_or_create(
        slug="feedback-ingestion",
        defaults={"display": "Feedback Ingestion", "is_enabled": False},
    )

    response = client.post(
        reverse("pages:user-story-submit"),
        data={"rating": 4, "comments": "Disabled", "path": "/"},
    )

    assert response.status_code == 404
    assert response.json()["success"] is False


def test_feedback_submission_marks_javascript_disabled_by_default(client, settings):
    """Feedback submissions without a JavaScript marker should store disabled status."""

    settings.USER_STORY_THROTTLE_SECONDS = 0

    response = client.post(
        reverse("pages:user-story-submit"),
        data={
            "rating": 4,
            "comments": "No script submit",
            "path": "/",
        },
    )

    assert response.status_code == 200
    story = UserStory.objects.latest("submitted_at")
    assert story.javascript_enabled is False


def test_feedback_submission_marks_javascript_enabled_when_flag_is_set(client, settings):
    """Feedback submissions should persist JavaScript-enabled marker from form data."""

    settings.USER_STORY_THROTTLE_SECONDS = 0

    response = client.post(
        reverse("pages:user-story-submit"),
        data={
            "rating": 4,
            "comments": "Script submit",
            "path": "/",
            "javascript_enabled": "1",
        },
    )

    assert response.status_code == 200
    story = UserStory.objects.latest("submitted_at")
    assert story.javascript_enabled is True


def test_authenticated_non_staff_feedback_limits_files(settings):
    """Non-staff authenticated users should be limited to configured attachment count."""

    settings.USER_STORY_ATTACHMENT_LIMIT = 2
    user = get_user_model().objects.create_user(
        username="member",
        email="member@example.com",
        password="secret",
    )

    form = UserStoryForm(
        data={
            "name": "member",
            "rating": 4,
            "comments": "Member feedback",
            "path": "/member",
            "messages": "",
        },
        files=MultiValueDict(
            {
                "attachments": [
                    SimpleUploadedFile("a.txt", b"a"),
                    SimpleUploadedFile("b.txt", b"b"),
                    SimpleUploadedFile("c.txt", b"c"),
                ]
            }
        ),
        user=user,
    )

    assert not form.is_valid()
    assert "attachments" in form.errors


def test_staff_feedback_supports_unlimited_text_and_files(settings):
    """Staff feedback should allow long comments and multiple attachments."""

    staff = get_user_model().objects.create_user(
        username="staff",
        email="staff@example.com",
        password="secret",
        is_staff=True,
    )

    form = UserStoryForm(
        data={
            "name": "staff",
            "rating": 2,
            "comments": "x" * 1000,
            "path": "/admin",
            "messages": "",
        },
        files=MultiValueDict(
            {
                "attachments": [
                    SimpleUploadedFile("one.txt", b"1"),
                    SimpleUploadedFile("two.txt", b"2"),
                    SimpleUploadedFile("three.txt", b"3"),
                ]
            }
        ),
        user=staff,
    )

    assert form.is_valid(), form.errors
    story = form.save()
    assert story.comments == "x" * 1000
    assert UserStoryAttachment.objects.filter(user_story=story).count() == 3


def test_form_enforces_comment_limit_for_non_staff():
    """Non-staff users should keep the 400-character feedback limit."""

    user = get_user_model().objects.create_user(
        username="commenter",
        email="commenter@example.com",
        password="secret",
    )
    form = UserStoryForm(
        data={
            "name": "commenter",
            "rating": 4,
            "comments": "x" * 401,
            "path": "/",
            "messages": "",
        },
        user=user,
    )

    assert not form.is_valid()
    assert "comments" in form.errors


def test_form_save_attachments_after_manual_instance_save(settings):
    """Attachments should persist even when the instance is saved with commit=False first."""

    staff = get_user_model().objects.create_user(
        username="manualsave",
        email="manualsave@example.com",
        password="secret",
        is_staff=True,
    )

    form = UserStoryForm(
        data={
            "name": "manualsave",
            "rating": 5,
            "comments": "Looks good",
            "path": "/manual",
            "messages": "",
        },
        files=MultiValueDict(
            {
                "attachments": [
                    SimpleUploadedFile("first.txt", b"1"),
                    SimpleUploadedFile("second.txt", b"2"),
                ]
            }
        ),
        user=staff,
    )

    assert form.is_valid(), form.errors
    story = form.save(commit=False)
    story.save()
    form.save_attachments()

    assert UserStoryAttachment.objects.filter(user_story=story).count() == 2


def test_attachment_limit_validation_message_uses_singular_for_one(settings):
    """Attachment count validation should use singular noun when the limit is one."""

    settings.USER_STORY_ATTACHMENT_LIMIT = 1
    user = get_user_model().objects.create_user(
        username="onefile",
        email="onefile@example.com",
        password="secret",
    )

    form = UserStoryForm(
        data={
            "name": "onefile",
            "rating": 4,
            "comments": "Member feedback",
            "path": "/member",
            "messages": "",
        },
        files=MultiValueDict(
            {
                "attachments": [
                    SimpleUploadedFile("a.txt", b"a"),
                    SimpleUploadedFile("b.txt", b"b"),
                ]
            }
        ),
        user=user,
    )

    assert not form.is_valid()
    assert "attachments" in form.errors
    assert "up to 1 file." in form.errors["attachments"][0]


def test_form_rejects_disallowed_attachment_extension(settings):
    """Attachments with non-whitelisted file extensions should be rejected."""

    settings.USER_STORY_ATTACHMENT_ALLOWED_EXTENSIONS = ("txt",)
    user = get_user_model().objects.create_user(
        username="extcheck",
        email="extcheck@example.com",
        password="secret",
    )

    form = UserStoryForm(
        data={
            "name": "extcheck",
            "rating": 4,
            "comments": "Member feedback",
            "path": "/member",
            "messages": "",
        },
        files=MultiValueDict(
            {
                "attachments": [
                    SimpleUploadedFile("bad.exe", b"nope"),
                ]
            }
        ),
        user=user,
    )

    assert not form.is_valid()
    assert "attachments" in form.errors
    assert "Unsupported file type" in form.errors["attachments"][0]


def test_form_rejects_oversized_attachments(settings):
    """Attachments larger than configured maximum should be rejected."""

    settings.USER_STORY_ATTACHMENT_MAX_BYTES = 1024
    user = get_user_model().objects.create_user(
        username="sizecheck",
        email="sizecheck@example.com",
        password="secret",
    )

    form = UserStoryForm(
        data={
            "name": "sizecheck",
            "rating": 4,
            "comments": "Member feedback",
            "path": "/member",
            "messages": "",
        },
        files=MultiValueDict(
            {
                "attachments": [
                    SimpleUploadedFile("large.txt", b"x" * 2048),
                ]
            }
        ),
        user=user,
    )

    assert not form.is_valid()
    assert "attachments" in form.errors
    assert "MB or smaller" in form.errors["attachments"][0]


def test_feedback_submission_updates_chat_profile_preference(client, settings):
    """Regression: feedback submissions should persist chat preference for authenticated users."""

    from apps.users.models import ChatProfile

    settings.USER_STORY_THROTTLE_SECONDS = 0
    user = get_user_model().objects.create_user(
        username="chat-opt-in",
        email="chat-opt-in@example.com",
        password="secret",
    )
    client.force_login(user)

    response = client.post(
        reverse("pages:user-story-submit"),
        data={
            "rating": 5,
            "comments": "Great page",
            "path": "/",
            "contact_via_chat": "1",
        },
    )

    assert response.status_code == 200
    profile = ChatProfile.objects.get(user=user)
    assert profile.contact_via_chat is True
    story = UserStory.objects.get(user=user)
    assert story.contact_via_chat is True


def test_user_story_form_prefills_chat_opt_in_for_authenticated_user():
    """Regression: feedback form should pre-check chat preference from chat profile."""

    from apps.users.models import ChatProfile

    user = get_user_model().objects.create_user(
        username="chat-profile-user",
        email="chat-profile-user@example.com",
        password="secret",
    )
    ChatProfile.objects.create(user=user, contact_via_chat=True)

    form = UserStoryForm(
        data={
            "name": user.username,
            "rating": 4,
            "comments": "Member feedback",
            "path": "/member",
            "messages": "",
        },
        files=MultiValueDict(),
        user=user,
    )

    assert form.fields["contact_via_chat"].initial is True


def test_user_story_form_prefill_chat_opt_in_handles_missing_profile_method():
    """Form prefill should gracefully handle users without profile helper support."""

    class MissingProfileUser:
        is_authenticated = True

        @staticmethod
        def get_username():
            return "no-profile-helper"

    user = MissingProfileUser()

    form = UserStoryForm(
        data={
            "name": user.get_username(),
            "rating": 4,
            "comments": "Member feedback",
            "path": "/member",
            "messages": "",
        },
        files=MultiValueDict(),
        user=user,
    )

    assert form.fields["contact_via_chat"].initial is False
