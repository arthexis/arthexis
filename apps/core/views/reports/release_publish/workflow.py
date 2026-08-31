"""Compatibility alias for release publish workflow orchestration."""

from __future__ import annotations

import sys

from apps.release.publishing import workflow as _workflow

sys.modules[__name__] = _workflow
