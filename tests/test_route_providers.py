"""Tests for route-provider registration behavior."""

from types import ModuleType
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import override_settings
from django.urls import path

from config import route_providers

def test_autodiscovered_route_patterns_uses_explicit_provider_list():
    routes_module = ModuleType("apps.example.routes")
    routes_module.ROOT_URLPATTERNS = [
        path("", lambda request: HttpResponse("ok"), name="example-home")
    ]

    def fake_import_module(module_name: str):
        if module_name == "apps.example.routes":
            return routes_module
        raise ModuleNotFoundError(module_name)

    with (
        override_settings(ROUTE_PROVIDERS=["apps.example.routes"]),
        patch.object(route_providers, "import_module", fake_import_module),
    ):
        patterns = route_providers.autodiscovered_route_patterns()

        assert len(patterns) == 1
        assert patterns[0].name == "example-home"


def test_autodiscovered_route_patterns_rejects_invalid_provider_settings():
    with override_settings(ROUTE_PROVIDERS="apps.example.routes"):
        with pytest.raises(ImproperlyConfigured):
            route_providers.autodiscovered_route_patterns()
