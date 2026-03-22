"""Regression tests for Docker Compose secret-key persistence."""

from __future__ import annotations

from pathlib import Path


def test_web_service_persists_locks_directory() -> None:
    """The web service should persist ``/app/.locks`` across recreations."""

    compose_text = Path("compose.yaml").read_text(encoding="utf-8")

    assert "- arthexis_locks:/app/.locks" in compose_text


def test_named_locks_volume_is_declared() -> None:
    """Compose should declare the named volume used for persisted lock files."""

    compose_text = Path("compose.yaml").read_text(encoding="utf-8")

    assert """volumes:
  arthexis_db:
  arthexis_locks:
  arthexis_media:""" in compose_text
