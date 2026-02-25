"""Tests for desktop registered extension model behavior."""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.desktop.models import RegisteredExtension


def test_build_runtime_command_uses_filename_sigil() -> None:
    """Filename sigil replacement should populate extra args by default."""

    extension = RegisteredExtension(
        extension=".txt",
        django_command="echo_filename",
        extra_args="--path {filename} --verbose",
        filename_sigil="{filename}",
        filename_as_input=False,
    )

    command, input_data = extension.build_runtime_command("/tmp/file.txt")

    assert command == ["echo_filename", "--path", "/tmp/file.txt", "--verbose"]
    assert input_data is None


def test_build_runtime_command_can_send_filename_as_input() -> None:
    """Filename can be provided via stdin when configured."""

    extension = RegisteredExtension(
        extension=".txt",
        django_command="echo_filename",
        extra_args="--stdin",
        filename_as_input=True,
    )

    command, input_data = extension.build_runtime_command("/tmp/file.txt")

    assert command == ["echo_filename", "--stdin"]
    assert input_data == "/tmp/file.txt"


def test_clean_rejects_extension_without_dot() -> None:
    """Extension validation must enforce a leading dot."""

    extension = RegisteredExtension(extension="txt", django_command="cmd")

    try:
        extension.clean()
    except ValidationError as exc:
        assert "extension" in exc.message_dict
    else:  # pragma: no cover - explicit regression guard
        raise AssertionError("Expected ValidationError for extension without leading dot")


def test_clean_rejects_extension_with_path_separator() -> None:
    """Extension must not allow path separators that can alter registry key paths."""

    extension = RegisteredExtension(extension=".foo\\bar", django_command="cmd")

    try:
        extension.clean()
    except ValidationError as exc:
        assert exc.message_dict["extension"] == [
            "Extension cannot include spaces or path separators."
        ]
    else:  # pragma: no cover - explicit regression guard
        raise AssertionError("Expected ValidationError for extension with path separator")


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
