"""Regression tests for feedback submission attachments."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.sites.forms import UserStoryForm
from apps.sites.models import UserStory, UserStoryAttachment


@pytest.mark.django_db
def test_anonymous_feedback_rejects_attachments(client, settings):
    """Regression: anonymous submitters must not be able to upload attachments."""

    settings.USER_STORY_THROTTLE_SECONDS = 0
    response = client.post(
        reverse("pages:user-story-submit"),
        data={
            "name": "anon@example.com",
            "rating": 4,
            "comments": "Anonymous feedback",
            "attachments": SimpleUploadedFile("anon.txt", b"data", content_type="text/plain"),
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert "attachments" in payload["errors"]
    assert UserStory.objects.count() == 0
    assert UserStoryAttachment.objects.count() == 0


@pytest.mark.django_db
def test_authenticated_non_staff_attachment_limit_enforced(client, settings):
    """Regression: authenticated non-staff users are limited to three attachments."""

    settings.USER_STORY_THROTTLE_SECONDS = 0
    user = get_user_model().objects.create_user(
        username="feedback-user", email="feedback-user@example.com", password="secret"
    )
    client.force_login(user)

    files = [
        SimpleUploadedFile(f"file-{index}.txt", b"data", content_type="text/plain")
        for index in range(UserStoryForm.MAX_NON_STAFF_ATTACHMENTS + 1)
    ]
    response = client.post(
        reverse("pages:user-story-submit"),
        data={
            "rating": 3,
            "comments": "Needs work",
            "attachments": files,
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert "attachments" in payload["errors"]
    assert UserStory.objects.count() == 0
    assert UserStoryAttachment.objects.count() == 0


@pytest.mark.django_db
def test_authenticated_non_staff_can_upload_up_to_three_attachments(client, settings):
    """Regression: authenticated non-staff users can submit up to three attachments."""

    settings.USER_STORY_THROTTLE_SECONDS = 0
    user = get_user_model().objects.create_user(
        username="feedback-user-ok", email="feedback-user-ok@example.com", password="secret"
    )
    client.force_login(user)

    files = [
        SimpleUploadedFile(f"ok-{index}.txt", b"data", content_type="text/plain")
        for index in range(UserStoryForm.MAX_NON_STAFF_ATTACHMENTS)
    ]
    response = client.post(
        reverse("pages:user-story-submit"),
        data={
            "rating": 5,
            "comments": "All good",
            "attachments": files,
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    story = UserStory.objects.get()
    assert story.attachments.count() == 3


@pytest.mark.django_db
def test_staff_feedback_allows_unlimited_attachments(client, settings):
    """Regression: staff users can submit more than the non-staff attachment limit."""

    settings.USER_STORY_THROTTLE_SECONDS = 0
    staff = get_user_model().objects.create_user(
        username="feedback-staff",
        email="feedback-staff@example.com",
        password="secret",
        is_staff=True,
    )
    client.force_login(staff)

    files = [
        SimpleUploadedFile(f"staff-{index}.txt", b"data", content_type="text/plain")
        for index in range(6)
    ]
    response = client.post(
        reverse("pages:user-story-submit"),
        data={
            "rating": 4,
            "comments": "Needs polish",
            "attachments": files,
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    story = UserStory.objects.get()
    assert story.attachments.count() == 6


@pytest.mark.django_db
def test_rating_five_feedback_with_attachments_does_not_enqueue_github_issue(client, settings, monkeypatch):
    """Regression: attachment flow should not enqueue GitHub issue creation for 5-star feedback."""

    settings.USER_STORY_THROTTLE_SECONDS = 0
    user = get_user_model().objects.create_user(
        username="feedback-five", email="feedback-five@example.com", password="secret"
    )
    client.force_login(user)

    enqueue_calls: list[int] = []

    def _capture_enqueue(self):
        enqueue_calls.append(self.pk)

    monkeypatch.setattr(UserStory, "enqueue_github_issue_creation", _capture_enqueue)

    response = client.post(
        reverse("pages:user-story-submit"),
        data={
            "rating": 5,
            "comments": "Great!",
            "attachments": [
                SimpleUploadedFile("ok.txt", b"data", content_type="text/plain")
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert enqueue_calls == []


@pytest.mark.django_db
def test_authenticated_feedback_rejects_disallowed_attachment_extension(client, settings):
    """Regression: authenticated users cannot upload executable or HTML-style attachments."""

    settings.USER_STORY_THROTTLE_SECONDS = 0
    user = get_user_model().objects.create_user(
        username="feedback-ext", email="feedback-ext@example.com", password="secret"
    )
    client.force_login(user)

    response = client.post(
        reverse("pages:user-story-submit"),
        data={
            "rating": 4,
            "comments": "Contains bad extension",
            "attachments": [
                SimpleUploadedFile("bad.html", b"<html></html>", content_type="text/html")
            ],
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert "attachments" in payload["errors"]
    assert UserStory.objects.count() == 0


@pytest.mark.django_db
def test_authenticated_feedback_rejects_oversized_attachment(client, settings):
    """Regression: attachment uploads enforce per-file size constraints."""

    settings.USER_STORY_THROTTLE_SECONDS = 0
    user = get_user_model().objects.create_user(
        username="feedback-size", email="feedback-size@example.com", password="secret"
    )
    client.force_login(user)

    too_large = b"a" * (UserStoryForm.MAX_ATTACHMENT_FILE_SIZE_BYTES + 1)
    response = client.post(
        reverse("pages:user-story-submit"),
        data={
            "rating": 2,
            "comments": "Large attachment",
            "attachments": [
                SimpleUploadedFile("large.txt", too_large, content_type="text/plain")
            ],
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert "attachments" in payload["errors"]
    assert UserStory.objects.count() == 0
