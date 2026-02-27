"""Tests for stored prompt model behavior."""

from apps.prompts.models import StoredPrompt


def test_stored_prompt_string_representation() -> None:
    """Stored prompts should display their title in admin contexts."""

    prompt = StoredPrompt(title="Prompt title", slug="prompt-title")

    assert str(prompt) == "Prompt title"


def test_stored_prompt_natural_key() -> None:
    """Stored prompts expose slug-based natural keys for fixtures."""

    prompt = StoredPrompt(title="Prompt title", slug="prompt-title")

    assert prompt.natural_key() == ("prompt-title",)
