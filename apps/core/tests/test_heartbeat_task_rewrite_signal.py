"""Regression tests for heartbeat rewrite signal behavior."""

from __future__ import annotations

import pytest

from apps.core import apps as core_apps

pytestmark = [pytest.mark.django_db]

