"""Tests for default ``runserver`` bind handling in ``manage.py``."""

from __future__ import annotations

import manage


def test_ensure_runserver_default_bind_adds_loopback_host() -> None:
    """Default ``runserver`` invocation should bind to IPv4 loopback only."""

    args = ["runserver"]

    manage._ensure_runserver_default_bind(args)

    assert args == ["runserver", "127.0.0.1:8888"]


def test_ensure_runserver_default_bind_preserves_explicit_host() -> None:
    """Explicit host bindings should remain unchanged."""

    args = ["runserver", "0.0.0.0:9000"]

    manage._ensure_runserver_default_bind(args)

    assert args == ["runserver", "0.0.0.0:9000"]


def test_ensure_runserver_default_bind_adds_ipv6_loopback_with_ipv6_flag() -> None:
    """IPv6 mode should default to the IPv6 loopback address only."""

    args = ["runserver", "--ipv6"]

    manage._ensure_runserver_default_bind(args)

    assert args == ["runserver", "--ipv6", "[::1]:8888"]


def test_ensure_runserver_default_bind_with_flags_only() -> None:
    """Flags without an addrport should still receive the safe default bind."""

    args = ["runserver", "--noreload", "--verbosity", "2"]

    manage._ensure_runserver_default_bind(args)

    assert args == ["runserver", "--noreload", "--verbosity", "2", "127.0.0.1:8888"]


def test_ensure_runserver_default_bind_preserves_port_only() -> None:
    """A positional port should continue to use Django's implicit localhost bind."""

    args = ["runserver", "9999"]

    manage._ensure_runserver_default_bind(args)

    assert args == ["runserver", "9999"]


def test_ensure_runserver_default_bind_preserves_addrport_flag() -> None:
    """The explicit ``--addrport`` flag should not be rewritten."""

    args = ["runserver", "--addrport", "0.0.0.0:7000"]

    manage._ensure_runserver_default_bind(args)

    assert args == ["runserver", "--addrport", "0.0.0.0:7000"]
