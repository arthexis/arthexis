"""Tests for prompt admin configuration."""

from django.contrib.admin.sites import AdminSite

from apps.prompts.admin import StoredPromptAdmin
from apps.prompts.models import StoredPrompt


def test_stored_prompt_admin_surfaces_change_reference():
    """Ensure the prompt admin exposes change references for browsing and search."""

    admin = StoredPromptAdmin(StoredPrompt, AdminSite())

    assert admin.list_display == ("title", "slug", "change_reference", "updated_at")
    assert admin.search_fields == (
        "title",
        "slug",
        "change_reference",
        "prompt_text",
        "initial_plan",
    )
