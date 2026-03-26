"""Tests for desktop model validation behavior."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.desktop.models import DesktopShortcut, RegisteredExtension


def test_registered_extension_clean_rejects_invalid_extension_chars() -> None:
    """Archived extension rows still enforce a basic extension format."""

    extension = RegisteredExtension(extension=".bad/name")

    with pytest.raises(ValidationError, match="Extension cannot include spaces"):
        extension.clean()


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
