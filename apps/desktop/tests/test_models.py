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
        extension.full_clean()
    except ValidationError as exc:
        assert "extension" in exc.message_dict
    else:  # pragma: no cover - explicit regression guard
        raise AssertionError("Expected ValidationError for extension without leading dot")
