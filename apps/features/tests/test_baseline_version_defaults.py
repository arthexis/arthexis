"""Regression coverage for suite feature baseline-version defaults."""

from __future__ import annotations

import pytest

from apps.features.management.feature_ops import apply_suite_feature_baseline_defaults
from apps.features.models import Feature

