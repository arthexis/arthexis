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


@pytest.mark.pr(6201)
def test_ensure_runserver_default_bind_adds_ipv6_host_with_ipv6_flag() -> None:
    """Runserver args with --ipv6 should default to an IPv6 wildcard bind."""

    args = ["runserver", "--ipv6"]
    manage._ensure_runserver_default_bind(args)
    assert args == ["runserver", "--ipv6", "[::]:8888"]


@pytest.mark.pr(6201)
def test_ensure_runserver_default_bind_with_flags_only() -> None:
    """Runserver args with flags and no addrport should still get default bind."""

    args = ["runserver", "--verbosity", "2"]
    manage._ensure_runserver_default_bind(args)
    assert args == ["runserver", "--verbosity", "2", "0.0.0.0:8888"]


@pytest.mark.pr(6201)
def test_ensure_runserver_default_bind_preserves_port_only() -> None:
    """Port-only argument should be preserved unchanged."""

    args = ["runserver", "8000"]
    manage._ensure_runserver_default_bind(args)
    assert args == ["runserver", "8000"]
