"""Regression tests for API explorer models."""

import json

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError

from apps.apis.models import APIExplorer, ResourceMethod


@pytest.mark.django_db
def test_resource_method_requires_leading_slash_regression() -> None:
    """Regression: resource paths should always be relative and slash-prefixed."""

    api = APIExplorer.objects.create(name="Billing", base_url="https://api.example.com")
    method = ResourceMethod(
        api=api,
        operation_name="Fetch invoice",
        resource_path="invoices/{id}",
        http_method=ResourceMethod.HttpMethod.GET,
    )

    with pytest.raises(ValidationError, match="must start with"):
        method.full_clean()


@pytest.mark.django_db
def test_resource_method_unique_operation_per_api_regression() -> None:
    """Regression: duplicate operation definitions should be rejected per API."""

    api = APIExplorer.objects.create(name="Users", base_url="https://users.example.com")
    ResourceMethod.objects.create(
        api=api,
        operation_name="List users",
        resource_path="/users",
        http_method=ResourceMethod.HttpMethod.GET,
    )

    with pytest.raises(IntegrityError):
        ResourceMethod.objects.create(
            api=api,
            operation_name="List users",
            resource_path="/users",
            http_method=ResourceMethod.HttpMethod.GET,
        )


@pytest.mark.django_db
def test_resource_method_str_includes_operation_context_regression() -> None:
    """Regression: resource methods should render with API, verb, path, and operation."""

    api = APIExplorer.objects.create(name="Inventory", base_url="https://inventory.example.com")
    resource_method = ResourceMethod.objects.create(
        api=api,
        operation_name="Get stock",
        resource_path="/stock/{sku}",
        http_method=ResourceMethod.HttpMethod.GET,
    )

    assert str(resource_method) == "Inventory: GET /stock/{sku} (Get stock)"


@pytest.mark.django_db
def test_resource_method_coerces_empty_json_structures_regression() -> None:
    """Regression: empty structure values should normalize to empty objects."""

    api = APIExplorer.objects.create(name="Orders", base_url="https://orders.example.com")
    method = ResourceMethod(
        api=api,
        operation_name="Create order",
        resource_path="/orders",
        http_method=ResourceMethod.HttpMethod.POST,
        request_structure=None,
        response_structure="",
    )

    method.full_clean()

    assert method.request_structure == {}
    assert method.response_structure == {}


@pytest.mark.django_db
def test_apiexplorer_natural_key_roundtrip_regression() -> None:
    """Regression: API explorers should resolve from fixture natural keys."""

    api = APIExplorer.objects.create(name="Evergo API Regression", base_url="https://portal.example.com")

    assert api.natural_key() == ("Evergo API Regression",)
    assert APIExplorer.objects.get_by_natural_key("Evergo API Regression") == api


@pytest.mark.django_db
def test_resource_method_natural_key_roundtrip_regression() -> None:
    """Regression: resource methods should resolve from fixture natural keys."""

    api = APIExplorer.objects.create(name="Natural Key API", base_url="https://example.com")
    method = ResourceMethod.objects.create(
        api=api,
        operation_name="Fetch",
        resource_path="/fetch",
        http_method=ResourceMethod.HttpMethod.GET,
    )

    assert method.natural_key() == ("Natural Key API", "/fetch", "GET", "Fetch")
    assert ResourceMethod.objects.get_by_natural_key("Natural Key API", "/fetch", "GET", "Fetch") == method


@pytest.mark.django_db
def test_resource_method_fixture_reload_is_idempotent_regression(tmp_path) -> None:
    """Regression: loading a natural-key fixture twice should not violate uniqueness."""

    api = APIExplorer.objects.create(name="Fixture API", base_url="https://fixture.example.com")
    fixture = tmp_path / "apis_fixture.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "model": "apis.resourcemethod",
                    "fields": {
                        "api": ["Fixture API"],
                        "operation_name": "Load once",
                        "resource_path": "/fixture",
                        "http_method": "GET",
                        "request_structure": {},
                        "response_structure": {},
                        "notes": "",
                        "created_at": "2025-01-01T00:00:00Z",
                        "updated_at": "2025-01-01T00:00:00Z",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    call_command("loaddata", str(fixture), verbosity=0)
    call_command("loaddata", str(fixture), verbosity=0)

    assert ResourceMethod.objects.filter(api=api).count() == 1
