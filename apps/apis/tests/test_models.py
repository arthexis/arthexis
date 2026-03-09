"""Regression tests for API explorer models."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.apis.models import APIExplorer, ResourceMethod


@pytest.mark.django_db
def test_resource_method_requires_leading_slash() -> None:
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
def test_resource_method_unique_operation_per_api() -> None:
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
def test_resource_method_str_includes_operation_context() -> None:
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
def test_resource_method_coerces_empty_json_structures() -> None:
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
def test_apiexplorer_natural_key_roundtrip() -> None:
    """Regression: API explorers should resolve from fixture natural keys."""

    api = APIExplorer.objects.create(name="Evergo API Regression", base_url="https://portal.example.com")

    assert api.natural_key() == ("Evergo API Regression",)
    assert APIExplorer.objects.get_by_natural_key("Evergo API Regression") == api
