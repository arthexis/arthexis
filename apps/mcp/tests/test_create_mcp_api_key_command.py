"""Regression tests for MCP API key generation command."""

from __future__ import annotations

from io import StringIO

import pytest

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.mcp.models import McpApiKey

pytestmark = pytest.mark.critical


@pytest.mark.django_db
def test_create_mcp_api_key_command_generates_key() -> None:
    """The create_mcp_api_key command should create a key and print plaintext once."""

    user = get_user_model().objects.create_user(username="api-user")
    out = StringIO()

    call_command(
        "create_mcp_api_key",
        "--username",
        user.username,
        "--label",
        "ci",
        "--expires-in-days",
        "30",
        stdout=out,
    )

    output = out.getvalue()
    assert "MCP API key created." in output
    assert "api_key=mcp_" in output
    assert McpApiKey.objects.filter(user=user, label="ci").count() == 1


@pytest.mark.django_db
def test_create_mcp_api_key_command_rejects_unknown_user() -> None:
    """The create_mcp_api_key command should fail for unknown users."""

    with pytest.raises(CommandError) as exc_info:
        call_command("create_mcp_api_key", "--username", "missing")

    assert "does not exist" in str(exc_info.value)
