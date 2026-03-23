"""Tests for default ``runserver`` bind handling in ``manage.py``."""

from __future__ import annotations

import manage
import pytest


@pytest.mark.parametrize(
    ("input_args", "expected_args"),
    [
        pytest.param(["runserver"], ["runserver", "127.0.0.1:8888"], id="default_ipv4"),
        pytest.param(["runserver", "--ipv6"], ["runserver", "--ipv6", "[::1]:8888"], id="default_ipv6"),
        pytest.param(["runserver", "-6"], ["runserver", "-6", "[::1]:8888"], id="short_ipv6"),
        pytest.param(
            ["runserver", "--noreload", "--verbosity", "2"],
            ["runserver", "--noreload", "--verbosity", "2", "127.0.0.1:8888"],
            id="flags_only",
        ),
    ],
)
def test_ensure_runserver_default_bind_adds_loopback(
    input_args: list[str], expected_args: list[str]
) -> None:
    """Default ``runserver`` invocations should receive a loopback addrport."""

    manage._ensure_runserver_default_bind(input_args)

    assert input_args == expected_args


@pytest.mark.parametrize(
    "input_args",
    [
        pytest.param(["runserver", "0.0.0.0:9000"], id="explicit_addrport"),
        pytest.param(["runserver", "9999"], id="port_only"),
        pytest.param(["runserver", "--addrport", "0.0.0.0:7000"], id="addrport_flag"),
        pytest.param(["runserver", "--addrport=0.0.0.0:7000"], id="addrport_equals"),
    ],
)
def test_ensure_runserver_default_bind_preserves_explicit_addrport(
    input_args: list[str],
) -> None:
    """Explicit addrport forms should remain unchanged."""

    original_args = list(input_args)

    manage._ensure_runserver_default_bind(input_args)

    assert input_args == original_args
