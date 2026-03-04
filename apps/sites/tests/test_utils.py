"""Tests for public sites utility helpers."""

from __future__ import annotations

import pytest
from django.test.client import RequestFactory

from apps.features.models import Feature
from apps.sites.utils import get_request_language_code


@pytest.mark.django_db
def test_get_request_language_code_uses_operator_interface_default_parameter():
    """Regression: fallback language should come from operator interface feature metadata."""

    Feature.objects.update_or_create(
        slug="operator-site-interface",
        defaults={
            "display": "Operator Site Interface",
            "is_enabled": True,
            "metadata": {"parameters": {"default_language": "it"}},
        },
    )
    request = RequestFactory().get("/")
    request.COOKIES = {}
    request.LANGUAGE_CODE = ""

    language = get_request_language_code(request)

    assert language == "it"


@pytest.mark.django_db
def test_get_request_language_code_prefers_session_language_over_feature_parameter():
    """Session language should take precedence over feature-configured default."""

    Feature.objects.update_or_create(
        slug="operator-site-interface",
        defaults={
            "display": "Operator Site Interface",
            "is_enabled": True,
            "metadata": {"parameters": {"default_language": "de"}},
        },
    )
    request = RequestFactory().get("/")
    request.session = {"_language": "es"}
    request.COOKIES = {}
    request.LANGUAGE_CODE = ""

    language = get_request_language_code(request)

    assert language == "es"
