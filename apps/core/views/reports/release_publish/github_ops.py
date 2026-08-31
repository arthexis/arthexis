"""Compatibility alias for release publish GitHub helpers."""

from __future__ import annotations

import sys

from apps.release.publishing import github_ops as _github_ops

sys.modules[__name__] = _github_ops
