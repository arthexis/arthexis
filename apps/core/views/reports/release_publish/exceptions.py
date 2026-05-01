"""Compatibility alias for release publish exceptions."""

from __future__ import annotations

import sys

from apps.release.publishing import exceptions as _exceptions

sys.modules[__name__] = _exceptions
