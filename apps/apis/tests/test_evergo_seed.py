"""Regression tests for Evergo API explorer seeded endpoints."""

import pytest

from apps.apis.models import APIExplorer, ResourceMethod
from apps.evergo.models import EvergoUser


@pytest.mark.django_db
def test_evergo_api_explorer_seeded_endpoints_regression() -> None:
    """Regression: migration should seed all Evergo integration endpoints."""

    api = APIExplorer.objects.get(name="Evergo API")
    methods = {
        (method.http_method, method.resource_path)
        for method in ResourceMethod.objects.filter(api=api)
    }

    assert api.base_url == "https://portal-backend.evergo.com/api/mex/v1/"
    assert methods == {
        ("POST", "/login"),
        ("GET", "/config/catalogs/sitios/all"),
        ("GET", "/ordenes/search-ingenieros"),
        ("GET", "/config/catalogs/orden-estatus"),
        ("GET", "/ordenes/instalador-coordinador"),
    }


@pytest.mark.django_db
def test_evergo_api_explorer_matches_model_endpoints_regression() -> None:
    """Regression: seeded API explorer routes should mirror Evergo model endpoint constants."""

    api = APIExplorer.objects.get(name="Evergo API")
    methods = ResourceMethod.objects.filter(api=api)
    seeded_paths = {method.resource_path for method in methods}

    assert EvergoUser.API_LOGIN_URL.endswith("/login")
    assert EvergoUser.API_SITIOS_URL.endswith("/config/catalogs/sitios/all")
    assert EvergoUser.API_INGENIEROS_URL.endswith("/ordenes/search-ingenieros")
    assert EvergoUser.API_ORDEN_ESTATUS_URL.endswith("/config/catalogs/orden-estatus")
    assert EvergoUser.API_ORDERS_URL.endswith("/ordenes/instalador-coordinador")

    assert seeded_paths == {
        "/login",
        "/config/catalogs/sitios/all",
        "/ordenes/search-ingenieros",
        "/config/catalogs/orden-estatus",
        "/ordenes/instalador-coordinador",
    }
