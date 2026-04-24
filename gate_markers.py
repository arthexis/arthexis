"""Shared pytest gate marker helpers.

Pytest marker names must be valid identifiers, so the ``gate.upgrade`` syntax
used in test modules maps to the registered ``gate_upgrade`` marker.
"""

from __future__ import annotations

import pytest


class _GateMarkers:
    upgrade = pytest.mark.gate_upgrade


gate = _GateMarkers()

__all__ = ["gate"]
