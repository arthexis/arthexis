"""Compatibility shim for legacy ``apps.release.release`` imports."""

from __future__ import annotations

import warnings

from apps.release import *  # noqa: F403

warnings.warn(
    "apps.release.release is deprecated; import from apps.release instead.",
    DeprecationWarning,
    stacklevel=2,
)
