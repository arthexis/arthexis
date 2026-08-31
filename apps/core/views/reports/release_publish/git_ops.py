"""Compatibility alias for release publish git helpers."""

from __future__ import annotations

import sys

from apps.release.publishing import git_ops as _git_ops

sys.modules[__name__] = _git_ops
