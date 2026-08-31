"""Compatibility alias for release publish context helpers."""

from __future__ import annotations

import sys

from apps.release.publishing import context as _context

sys.modules[__name__] = _context
