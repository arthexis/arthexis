import pytest

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
