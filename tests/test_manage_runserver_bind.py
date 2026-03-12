"""Tests for default runserver binding behavior."""

from __future__ import annotations

import pytest

import manage


@pytest.mark.pr(6201)
def test_ensure_runserver_default_bind_adds_zero_host() -> None:
    """Runserver args without addrport should default to 0.0.0.0:8888."""

    args = ["runserver"]
    manage._ensure_runserver_default_bind(args)
    assert args == ["runserver", "0.0.0.0:8888"]


@pytest.mark.pr(6201)
def test_ensure_runserver_default_bind_preserves_explicit_host() -> None:
    """Explicit addrport arguments should be left unchanged."""

    args = ["runserver", "127.0.0.1:9000"]
    manage._ensure_runserver_default_bind(args)
    assert args == ["runserver", "127.0.0.1:9000"]
