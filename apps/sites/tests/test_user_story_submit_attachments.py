"""Regression tests for feedback submission attachments."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

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
        for index in range(4)
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
        for index in range(3)
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
