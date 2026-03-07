"""Tests for the admin management command."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings


def test_admin_set_persists_values_to_env(tmp_path):
    """Set action should persist normalized and custom values into arthexis.env."""

    with override_settings(BASE_DIR=str(tmp_path)):
        call_command(
            "admin",
            "set",
            path="/control-panel/",
            header="Control Room",
            title="Control Site",
            index_title="Backoffice",
        )

        env_contents = (Path(tmp_path) / "arthexis.env").read_text(encoding="utf-8")

    assert 'ADMIN_URL_PATH="control-panel/"' in env_contents
    assert 'ADMIN_SITE_HEADER="Control Room"' in env_contents
    assert 'ADMIN_SITE_TITLE="Control Site"' in env_contents
    assert 'ADMIN_INDEX_TITLE="Backoffice"' in env_contents


def test_admin_reset_removes_configured_keys(tmp_path):
    """Reset action should delete requested keys from arthexis.env."""

    with override_settings(BASE_DIR=str(tmp_path)):
        call_command(
            "set_env",
            set=[
                ["ADMIN_URL_PATH", "admin/"],
                ["ADMIN_SITE_HEADER", "Constellation"],
                ["UNRELATED", "keep"],
            ],
        )

        call_command("admin", "reset", all=True)

        env_contents = (Path(tmp_path) / "arthexis.env").read_text(encoding="utf-8")

    assert "ADMIN_URL_PATH" not in env_contents
    assert "ADMIN_SITE_HEADER" not in env_contents
    assert 'UNRELATED="keep"' in env_contents


def test_admin_set_rejects_invalid_path(tmp_path):
    """Set action should raise ``CommandError`` for invalid admin URL paths."""

    with override_settings(BASE_DIR=str(tmp_path)):
        with pytest.raises(CommandError, match="Admin URL path"):
            call_command("admin", "set", path="///")
