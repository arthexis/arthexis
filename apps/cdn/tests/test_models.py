"""Tests for CDN provider configuration validation."""

import pytest
from django.db import IntegrityError

from apps.cdn.models import CDNConfiguration


@pytest.mark.django_db
def test_database_constraint_enforces_provider_distribution_invariant():
    """DB check constraint rejects provider/distribution mismatch writes."""

    with pytest.raises(IntegrityError):
        CDNConfiguration.objects.create(
            name="Broken CDN",
            provider=CDNConfiguration.Provider.CLOUDFLARE,
            base_url="https://cdn.example.com/static/",
            aws_distribution_id="E123ABC",
        )
