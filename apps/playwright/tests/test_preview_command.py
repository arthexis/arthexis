from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.playwright.management.commands.preview import Command
def test_handle_reports_engine_failures_without_name_error(monkeypatch) -> None:
    """Engine failure aggregation should raise a clean CommandError message."""

    command = Command()

    monkeypatch.setattr(command, "_ensure_admin_user", lambda **kwargs: None)
    monkeypatch.setattr(command, "_build_capture_plan", lambda **kwargs: [])

    def _always_fail(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(command, "_capture_all", _always_fail)

    with pytest.raises(CommandError, match=r"All preview engines failed\. Last error: boom"):
        command.handle(
            base_url="http://127.0.0.1:8000",
            paths=["/admin/"],
            username="admin",
            password="admin123",
            output="media/previews/admin-preview.png",
            output_dir="",
            viewports="desktop",
            engine="chromium,firefox",
        )
