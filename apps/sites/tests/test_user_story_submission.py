"""Regression tests for user story feedback submission rules."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils.datastructures import MultiValueDict

from apps.sites.forms import UserStoryForm
from apps.sites.models import UserStory, UserStoryAttachment


@pytest.mark.django_db
@pytest.mark.regression
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


@pytest.mark.django_db
@pytest.mark.regression
def test_authenticated_non_staff_feedback_limits_files(settings):
    """Non-staff authenticated users should be limited to configured attachment count."""

    settings.USER_STORY_ATTACHMENT_LIMIT = 2
    user = get_user_model().objects.create_user(
        username="member",
        email="member@example.com",
        password="secret",
    )

    form = UserStoryForm(
        data={"name": "member", "rating": 4, "comments": "Member feedback", "path": "/member", "messages": ""},
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


@pytest.mark.django_db
@pytest.mark.regression
def test_staff_feedback_supports_unlimited_text_and_files(settings):
    """Staff feedback should allow long comments and multiple attachments."""

    staff = get_user_model().objects.create_user(
        username="staff",
        email="staff@example.com",
        password="secret",
        is_staff=True,
    )

    form = UserStoryForm(
        data={"name": "staff", "rating": 2, "comments": "x" * 1000, "path": "/admin", "messages": ""},
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


@pytest.mark.django_db
@pytest.mark.regression
def test_form_enforces_comment_limit_for_non_staff():
    """Non-staff users should keep the 400-character feedback limit."""

    user = get_user_model().objects.create_user(
        username="commenter",
        email="commenter@example.com",
        password="secret",
    )
    form = UserStoryForm(
        data={"name": "commenter", "rating": 4, "comments": "x" * 401, "path": "/", "messages": ""},
        user=user,
    )

    assert not form.is_valid()
    assert "comments" in form.errors
