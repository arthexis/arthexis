"""Regression tests for feedback form submissions."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.sites.models import UserStory, UserStoryAttachment


@pytest.fixture(autouse=True)
def _disable_user_story_throttle(settings):
    """Disable throttling so submission tests can post repeatedly."""

    settings.USER_STORY_THROTTLE_SECONDS = 0



@pytest.mark.django_db
def test_regression_anonymous_submission_cannot_upload_attachments(client):
    """Anonymous submissions should ignore any uploaded attachment files."""

    payload = {
        "name": "anon@example.com",
        "rating": "4",
        "comments": "Anonymous feedback",
        "path": "/",
        "messages": "",
        "attachments": SimpleUploadedFile("anon.txt", b"anonymous"),
    }

    response = client.post(reverse("pages:user-story-submit"), data=payload)

    assert response.status_code == 200
    assert response.json()["success"] is True
    story = UserStory.objects.get()
    assert story.user is None
    assert UserStoryAttachment.objects.count() == 0


@pytest.mark.django_db
def test_regression_non_staff_submission_limits_attachments(client):
    """Non-staff users should be limited to a small number of attachments."""

    user = get_user_model().objects.create_user(
        username="submitter",
        email="submitter@example.com",
        password="secret",
    )
    client.force_login(user)

    payload = {
        "rating": "3",
        "comments": "Needs improvement",
        "path": "/private/",
        "messages": "",
        "attachments": [
            SimpleUploadedFile("a.txt", b"a"),
            SimpleUploadedFile("b.txt", b"b"),
            SimpleUploadedFile("c.txt", b"c"),
            SimpleUploadedFile("d.txt", b"d"),
        ],
    }

    response = client.post(reverse("pages:user-story-submit"), data=payload)

    assert response.status_code == 400
    assert "attachments" in response.json()["errors"]
    assert UserStory.objects.count() == 0


@pytest.mark.django_db
def test_regression_staff_submission_allows_unlimited_attachments_and_long_comments(client):
    """Staff users should bypass comment and attachment count limits."""

    user = get_user_model().objects.create_user(
        username="staffer",
        email="staffer@example.com",
        password="secret",
        is_staff=True,
    )
    client.force_login(user)
    comments = "x" * 401
    uploads = [
        SimpleUploadedFile(f"{index}.txt", str(index).encode("utf-8"))
        for index in range(5)
    ]

    response = client.post(
        reverse("pages:user-story-submit"),
        data={
            "rating": "5",
            "comments": comments,
            "path": "/admin/",
            "messages": "",
            "attachments": uploads,
        },
    )

    assert response.status_code == 200
    story = UserStory.objects.get()
    assert story.comments == comments
    assert UserStoryAttachment.objects.filter(user_story=story).count() == 5
