"""Tests for CDN provider configuration validation."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.cdn.models import CDNConfiguration


def test_aws_cloudfront_requires_distribution_id():
    """AWS CloudFront configurations must include a distribution ID."""

    config = CDNConfiguration(
        name="Production CDN",
        provider=CDNConfiguration.Provider.AWS_CLOUDFRONT,
        base_url="https://d111111abcdef8.cloudfront.net/static/",
    )

    try:
        config.clean()
    except ValidationError as error:
        assert "aws_distribution_id" in error.message_dict
    else:  # pragma: no cover - explicit guard
        raise AssertionError("Expected clean to raise ValidationError")


def test_non_aws_provider_rejects_distribution_id():
    """Non-AWS providers should not retain CloudFront-specific distribution IDs."""

    config = CDNConfiguration(
        name="Edge CDN",
        provider=CDNConfiguration.Provider.CLOUDFLARE,
        base_url="https://cdn.example.com/static/",
        aws_distribution_id="E123ABC",
    )

    try:
        config.clean()
    except ValidationError as error:
        assert "aws_distribution_id" in error.message_dict
    else:  # pragma: no cover - explicit guard
        raise AssertionError("Expected clean to raise ValidationError")


def test_base_url_requires_https_scheme():
    """CDN base URLs should only allow secure HTTPS URLs."""

    config = CDNConfiguration(
        name="Insecure CDN",
        provider=CDNConfiguration.Provider.CLOUDFLARE,
        base_url="http://cdn.example.com/static/",
    )

    try:
        config.clean_fields()
    except ValidationError as error:
        assert "base_url" in error.message_dict
    else:  # pragma: no cover - explicit guard
        raise AssertionError("Expected full_clean to raise ValidationError")


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


def test_whitespace_distribution_id_is_treated_as_blank():
    """Whitespace-only distribution IDs should be normalized before validation."""

    config = CDNConfiguration(
        name="Whitespace AWS CDN",
        provider=CDNConfiguration.Provider.AWS_CLOUDFRONT,
        base_url="https://d111111abcdef8.cloudfront.net/static/",
        aws_distribution_id="   ",
    )

    with pytest.raises(ValidationError):
        config.clean()


@pytest.mark.django_db
def test_database_constraint_requires_https_base_url():
    """DB check constraint rejects non-HTTPS base URLs."""

    with pytest.raises(IntegrityError):
        CDNConfiguration.objects.create(
            name="Broken HTTP CDN",
            provider=CDNConfiguration.Provider.CLOUDFLARE,
            base_url="http://cdn.example.com/static/",
        )
