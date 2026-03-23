"""Tests for desktop model validation behavior."""

from __future__ import annotations

import pytest
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


def test_desktop_shortcut_clean_requires_absolute_http_url() -> None:
    """Desktop shortcuts must always point at an absolute HTTP(S) URL."""

    shortcut = DesktopShortcut(
        slug="public-site",
        desktop_filename="Arthexis Public Site",
        name="Arthexis Public Site",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="/relative/path",
    )

    with pytest.raises(ValidationError) as exc_info:
        shortcut.clean()

    assert exc_info.value.message_dict["target_url"] == [
        "Target URL must be an absolute http:// or https:// URL."
    ]


def test_desktop_shortcut_clean_rejects_non_url_launch_mode() -> None:
    """Desktop shortcuts reject legacy non-URL launch modes during validation."""

    shortcut = DesktopShortcut(
        slug="legacy-command",
        desktop_filename="Legacy Command",
        name="Legacy Command",
        launch_mode="command",
        target_url="http://127.0.0.1:{port}/",
    )

    with pytest.raises(ValidationError) as exc_info:
        shortcut.clean()

    assert exc_info.value.message_dict["launch_mode"] == [
        "Desktop shortcuts must use URL launch mode."
    ]


def test_desktop_shortcut_clean_rejects_unsafe_condition_expression() -> None:
    """Condition expressions cannot call arbitrary helpers or names."""

    shortcut = DesktopShortcut(
        slug="unsafe-expression",
        desktop_filename="Unsafe Expression",
        name="Unsafe Expression",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="http://127.0.0.1:{port}/",
        condition_expression="__import__('os').system('whoami')",
    )

    with pytest.raises(ValidationError) as exc_info:
        shortcut.clean()

    assert exc_info.value.message_dict["condition_expression"] == [
        "Condition expressions may only call has_feature(...)."
    ]


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
