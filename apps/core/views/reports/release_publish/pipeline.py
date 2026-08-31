"""Compatibility alias for the release publish pipeline implementation."""

from __future__ import annotations

import sys

from apps.release.publishing import pipeline as _pipeline

sys.modules[__name__] = _pipeline
