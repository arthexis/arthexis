"""Regression placeholder for removed resolve subcommand compatibility tests.

The previous resolve subcommand parity tests were removed because they depended on
a shell entrypoint behavior that is no longer available in this environment.
"""

from __future__ import annotations

import pytest

from utils import command_api


pytestmark = [
    pytest.mark.pr("PR-5652", "2026-02-26T00:00:00Z"),
]


def test_resolve_token_is_treated_as_legacy_command_name() -> None:
    """`resolve` should remain executable as a Django command via legacy parsing."""
    translated = command_api.parse_legacy_args(["resolve", "--target", "alpha"])
    assert translated == ["run", "resolve", "--target", "alpha"]
