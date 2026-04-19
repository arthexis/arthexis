"""Tests for CDN provider configuration validation."""

import pytest
from django.db import IntegrityError

from apps.cdn.models import CDNConfiguration

def test_jsdelivr_configuration_is_valid_without_distribution_id():
    """Free jsDelivr provider should validate without AWS-only fields."""

    config = CDNConfiguration(
        name="jsDelivr Mirror",
        provider=CDNConfiguration.Provider.JSDELIVR,
        base_url="https://cdn.jsdelivr.net/npm/my-package@1.0.0/",
    )

    config.clean()
    assert config.is_enabled is True


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


@pytest.mark.django_db
def test_database_constraint_requires_distribution_id_for_aws():
    """DB check constraint rejects AWS records missing distribution IDs."""

    with pytest.raises(IntegrityError):
        CDNConfiguration.objects.create(
            name="Broken AWS CDN",
            provider=CDNConfiguration.Provider.AWS_CLOUDFRONT,
            base_url="https://d111111abcdef8.cloudfront.net/static/",
        )


@pytest.mark.django_db
def test_database_constraint_requires_https_base_url():
    """DB check constraint rejects non-HTTPS base URLs."""

    with pytest.raises(IntegrityError):
        CDNConfiguration.objects.create(
            name="Broken HTTP CDN",
            provider=CDNConfiguration.Provider.CLOUDFLARE,
            base_url="http://cdn.example.com/static/",
        )
