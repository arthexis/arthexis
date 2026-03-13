"""Regression tests for user story feedback submission rules."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils.datastructures import MultiValueDict

from apps.features.models import Feature
from apps.sites.forms import UserStoryForm
from apps.sites.models import UserStory, UserStoryAttachment

pytestmark = [pytest.mark.django_db]


def make_png_bytes() -> bytes:
    """Create an in-memory PNG for upload tests."""

    image = Image.new("RGB", (1, 1), color=(255, 0, 0))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


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


@pytest.mark.pr_origin(6177)
def test_form_rejects_invalid_screenshot_content_type(settings):
    """Screenshot uploads should reject valid images with disallowed MIME types.

    Parameters:
        settings: Django settings fixture.

    Returns:
        None
    """

    settings.USER_STORY_SCREENSHOT_ALLOWED_CONTENT_TYPES = ["image/png"]

    form = UserStoryForm(
        data={
            "name": "anon@example.com",
            "rating": 4,
            "comments": "Screenshot upload",
            "path": "/",
            "messages": "",
        },
        files=MultiValueDict(
            {
                "screenshot": [
                    SimpleUploadedFile(
                        "screenshot.png",
                        make_png_bytes(),
                        content_type="text/plain",
                    ),
                ]
            }
        ),
    )

    assert not form.is_valid()
    assert "screenshot" in form.errors
    assert any(error.code == "invalid_screenshot_content_type" for error in form.errors.as_data()["screenshot"])


@pytest.mark.pr_origin(6177)
def test_form_rejects_oversized_screenshot(settings):
    """Screenshot uploads should reject files larger than configured maximum.

    Parameters:
        settings: Django settings fixture.

    Returns:
        None
    """

    settings.USER_STORY_SCREENSHOT_MAX_BYTES = 1024

    form = UserStoryForm(
        data={
            "name": "anon@example.com",
            "rating": 4,
            "comments": "Screenshot upload",
            "path": "/",
            "messages": "",
        },
        files=MultiValueDict(
            {
                "screenshot": [
                    SimpleUploadedFile("screenshot.png", make_png_bytes() * 2048, content_type="image/png"),
                ]
            }
        ),
    )

    assert not form.is_valid()
    assert "screenshot" in form.errors
    assert "Screenshot must be" in form.errors["screenshot"][0]


@pytest.mark.pr_origin(6182)
def test_form_rejects_non_image_screenshot_payload():
    """Screenshot field should reject payloads that are not valid images."""

    user = get_user_model().objects.create_user(
        username="screenshot-user",
        email="screenshot-user@example.com",
        password="secret",
    )

    form = UserStoryForm(
        data={
            "name": "anon@example.com",
            "rating": 4,
            "comments": "Screenshot upload",
            "path": "/",
            "messages": "",
        },
        files=MultiValueDict(
            {
                "screenshot": [
                    SimpleUploadedFile("screenshot.png", b"not-a-real-image", content_type="image/png"),
                ]
            }
        ),
        user=user,
    )

    assert not form.is_valid()
    assert "screenshot" in form.errors


@pytest.mark.pr_origin(6182)
def test_form_accepts_valid_image_screenshot():
    """Screenshot field should accept valid image payloads."""

    user = get_user_model().objects.create_user(
        username="valid-screenshot-user",
        email="valid-screenshot-user@example.com",
        password="secret",
    )

    image_file = io.BytesIO()
    Image.new("RGB", (2, 2), color="red").save(image_file, format="PNG")
    image_file.seek(0)

    form = UserStoryForm(
        data={
            "name": "anon@example.com",
            "rating": 4,
            "comments": "Screenshot upload",
            "path": "/",
            "messages": "",
        },
        files=MultiValueDict(
            {
                "screenshot": [
                    SimpleUploadedFile("screenshot.png", image_file.read(), content_type="image/png"),
                ]
            }
        ),
        user=user,
    )

    assert form.is_valid(), form.errors
