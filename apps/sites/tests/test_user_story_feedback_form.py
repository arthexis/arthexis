from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
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

