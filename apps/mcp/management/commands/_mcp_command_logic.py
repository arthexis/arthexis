from __future__ import annotations

"""Shared business logic for MCP management commands."""

import os
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.utils import timezone

from apps.mcp.models import McpApiKey
from apps.mcp.server import run_stdio_server


def parse_csv_names(raw_value: str | None) -> set[str]:
    """Parse a comma-separated list into normalized non-empty tool names."""

    if not raw_value:
        return set()
    normalized: set[str] = set()
    for value in raw_value.split(","):
        item = value.strip()
        if item:
            normalized.add(item)
    return normalized


def run_mcp_server(*, allow_raw: str | None, deny_raw: str | None) -> None:
    """Run the MCP stdio server with merged CLI and environment filters."""

    allow = parse_csv_names(allow_raw)
    deny = parse_csv_names(deny_raw)

    allow_from_env = parse_csv_names(os.getenv("ARTHEXIS_MCP_TOOLS_ALLOW"))
    deny_from_env = parse_csv_names(os.getenv("ARTHEXIS_MCP_TOOLS_DENY"))

    allow.update(allow_from_env)
    deny.update(deny_from_env)

    if allow and deny and allow.issubset(deny):
        raise CommandError("The deny-list blocks every allowed MCP tool.")

    run_stdio_server(allow=allow, deny=deny)


def create_mcp_api_key(*, username: str, label: str, expires_in_days: int) -> tuple[str, str, str, str]:
    """Create an MCP API key and return display metadata plus plaintext key."""

    normalized_label = label.strip()

    if not normalized_label:
        raise CommandError("--label must not be empty.")
    if expires_in_days < 0:
        raise CommandError("--expires-in-days must be zero or greater.")

    user_model = get_user_model()
    try:
        user = user_model.objects.get(username=username)
    except user_model.DoesNotExist as exc:
        raise CommandError(f"User '{username}' does not exist.") from exc

    expires_at = None
    if expires_in_days > 0:
        expires_at = timezone.now() + timedelta(days=expires_in_days)

    _api_key, plain_key = McpApiKey.objects.create_for_user(
        user=user,
        label=normalized_label,
        expires_at=expires_at,
    )

    expires_at_text = expires_at.isoformat() if expires_at else "never"
    return user.get_username(), normalized_label, expires_at_text, plain_key
