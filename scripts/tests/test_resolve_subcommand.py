"""Regression placeholder for removed resolve subcommand compatibility tests.

The previous resolve subcommand parity tests were removed because they depended on
a shell entrypoint behavior that is no longer available in this environment.
"""

from __future__ import annotations

import pytest


pytestmark = [
    pytest.mark.regression,
    pytest.mark.pr("PR-5652", "2026-02-26T00:00:00Z"),
]
