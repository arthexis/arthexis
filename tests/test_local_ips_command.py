"""Tests for the ``local-ips`` management command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def _read_addresses(base_dir: Path) -> list[str]:
    """Read the addresses list from ``.locks/local_ips.lck``."""

    payload = json.loads((base_dir / ".locks" / "local_ips.lck").read_text(encoding="utf-8"))
    return payload["addresses"]


def test_local_ips_add_creates_lock_file(tmp_path: Path, settings) -> None:
    """Adding addresses should create and persist the lock file payload."""

    settings.BASE_DIR = tmp_path

    call_command("local-ips", "--add", "10.0.0.10", "--add", "[::1]")

    assert _read_addresses(tmp_path) == ["10.0.0.10", "::1"]


def test_local_ips_remove_drops_existing_value(tmp_path: Path, settings) -> None:
    """Removing addresses should keep non-target addresses untouched."""

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True)
    (lock_dir / "local_ips.lck").write_text(
        json.dumps({"addresses": ["127.0.0.1", "10.0.0.10"], "updated_at": "existing"}),
        encoding="utf-8",
    )
    settings.BASE_DIR = tmp_path

    call_command("local-ips", "--remove", "127.0.0.1")

    assert _read_addresses(tmp_path) == ["10.0.0.10"]


def test_local_ips_requires_mutation_arguments(tmp_path: Path, settings) -> None:
    """The command should fail fast when no add/remove arguments are given."""

    settings.BASE_DIR = tmp_path

    with pytest.raises(CommandError, match="Provide at least one --add or --remove value"):
        call_command("local-ips")
