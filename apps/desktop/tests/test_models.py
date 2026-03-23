"""Tests for desktop model validation behavior."""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.desktop.models import DesktopShortcut, RegisteredExtension


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


def test_desktop_shortcut_clean_rejects_missing_url_in_url_mode() -> None:
    """URL launch mode requires a non-empty target URL."""

    shortcut = DesktopShortcut(
        slug="public-site",
        desktop_filename="Arthexis Public Site",
        name="Arthexis Public Site",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="",
    )

    try:
        shortcut.clean()
    except ValidationError as exc:
        assert exc.message_dict["target_url"] == ["A target URL is required."]
    else:  # pragma: no cover - explicit regression guard
        raise AssertionError("Expected ValidationError for blank target URL")


def test_desktop_shortcut_clean_rejects_icon_name_and_base64_together() -> None:
    """Desktop icons cannot use both system icon names and base64 payloads."""

    shortcut = DesktopShortcut(
        slug="public-site",
        desktop_filename="Arthexis Public Site",
        name="Arthexis Public Site",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="http://127.0.0.1:{port}/",
        icon_name="web-browser",
        icon_base64="aGVsbG8=",
    )

    try:
        shortcut.clean()
    except ValidationError as exc:
        assert exc.message_dict["icon_base64"] == [
            "Choose either icon base64 payload or icon name, not both."
        ]
    else:  # pragma: no cover - explicit regression guard
        raise AssertionError("Expected ValidationError for conflicting icon fields")
