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
@pytest.mark.parametrize(
    "create_kwargs",
    [
        {
            "name": "Broken CDN",
            "provider": CDNConfiguration.Provider.CLOUDFLARE,
            "base_url": "https://cdn.example.com/static/",
            "aws_distribution_id": "E123ABC",
        },
        {
            "name": "Broken AWS CDN",
            "provider": CDNConfiguration.Provider.AWS_CLOUDFRONT,
            "base_url": "https://d111111abcdef8.cloudfront.net/static/",
        },
        {
            "name": "Broken HTTP CDN",
            "provider": CDNConfiguration.Provider.CLOUDFLARE,
            "base_url": "http://cdn.example.com/static/",
        },
    ],
)
def test_database_constraints_reject_invalid_cdn_configuration(create_kwargs):
    """DB check constraints reject invalid provider/distribution/url combinations."""
    with pytest.raises(IntegrityError):
        CDNConfiguration.objects.create(**create_kwargs)
