"""Tests for liboqs admin registration."""

from __future__ import annotations

import pytest
from django.contrib import admin

from apps.liboqs.admin import OqsAlgorithmAdmin
from apps.liboqs.models import OqsAlgorithm


@pytest.mark.critical
def test_oqs_algorithm_admin_registered() -> None:
    """Regression: OqsAlgorithm should remain available in Django admin."""

    registered_admin = admin.site._registry.get(OqsAlgorithm)

    assert isinstance(registered_admin, OqsAlgorithmAdmin)
