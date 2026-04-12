import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import RequestFactory

from apps.sites.forms import UserStoryForm
from apps.sites.models import UserStory

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


def test_user_story_issue_body_omits_javascript_enabled_line():
    story = UserStory.objects.create(
        name="feedback@example.com",
        rating=3,
        comments="Flow felt confusing.",
        path="/dashboard/",
        contact_via_chat=True,
        javascript_enabled=True,
    )

    issue_body = story.build_github_issue_body()

    assert "**Contact via chat:** Yes" in issue_body
    assert "JavaScript enabled" not in issue_body


def test_user_story_feedback_template_omits_security_groups_for_non_staff_users():
    user = get_user_model().objects.create_user(
        username="regular-user",
        password="x",
        email="regular-user@example.com",
    )
    request = RequestFactory().get("/admin/")
    request.user = user

    html = render_to_string("admin/includes/user_story_feedback.html", request=request)

    assert 'data-security-groups=""' in html


def test_user_story_feedback_template_enables_comments_autocomplete():
    user = get_user_model().objects.create_user(
        username="staff-user",
        password="x",
        email="staff-user@example.com",
        is_staff=True,
    )
    request = RequestFactory().get("/admin/")
    request.user = user

    html = render_to_string("admin/includes/user_story_feedback.html", request=request)

    assert 'name="comments"' in html
    assert 'autocomplete="on"' in html


def test_public_feedback_template_enables_comments_autocomplete():
    request = RequestFactory().get("/")
    request.user = get_user_model()()

    html = render_to_string(
        "pages/includes/public_feedback_widget.html",
        {"feedback_ingestion_enabled": True},
        request=request,
    )

    assert 'name="comments"' in html
    assert 'autocomplete="on"' in html
