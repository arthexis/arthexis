import pytest

from apps.sites.forms import UserStoryForm

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
            "path": "/gallery/images/example/",
            "messages": "Existing page message",
            "feedback_context": "Image ID: 42 | Image UUID: abc",
        }
    )

    assert form.is_valid(), form.errors

    story = form.save()

    assert story.messages == "Existing page message | Context: Image ID: 42 | Image UUID: abc"
