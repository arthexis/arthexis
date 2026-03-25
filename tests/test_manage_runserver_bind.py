"""Regression tests for default bind behavior in manage.py runserver handling."""

import manage
import pytest


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["runserver"], ["runserver", "127.0.0.1:8888"]),
        (["runserver", "--ipv6"], ["runserver", "--ipv6", "[::1]:8888"]),
        (["runserver", "0.0.0.0:9000"], ["runserver", "0.0.0.0:9000"]),
        (["runserver", "--addrport", "0.0.0.0:9000"], ["runserver", "--addrport", "0.0.0.0:9000"]),
    ],
)
def test_ensure_runserver_default_bind_preserves_or_injects_expected_addrport(args, expected):
    """runserver should default to loopback while preserving explicit bindings."""

    actual = list(args)
    manage._ensure_runserver_default_bind(actual)

    assert actual == expected
