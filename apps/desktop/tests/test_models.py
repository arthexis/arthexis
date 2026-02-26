"""Tests for desktop registered extension model behavior."""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.desktop.models import RegisteredExtension

def test_clean_allows_blank_sigil_when_filename_as_input_enabled() -> None:
    """Filename sigil can be blank when filename is supplied via stdin."""

    extension = RegisteredExtension(
        extension=".txt",
        django_command="cmd",
        filename_as_input=True,
        filename_sigil="",
    )

    extension.clean()

def test_clean_rejects_blank_sigil_when_replacement_mode_enabled() -> None:
    """Filename sigil cannot be blank when argument replacement mode is enabled."""

    extension = RegisteredExtension(
        extension=".txt",
        django_command="cmd",
        filename_as_input=False,
        filename_sigil="   ",
    )

    try:
        extension.clean()
    except ValidationError as exc:
        assert exc.message_dict["filename_sigil"] == ["Filename sigil cannot be empty."]
    else:  # pragma: no cover - explicit regression guard
        raise AssertionError("Expected ValidationError for blank filename sigil")
