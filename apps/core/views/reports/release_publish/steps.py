"""Compatibility alias for release publish step primitives."""

from __future__ import annotations

import sys

from apps.release.publishing import steps as _steps

sys.modules[__name__] = _steps
