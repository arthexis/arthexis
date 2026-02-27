"""Regression tests for Evergo API explorer seeded endpoints."""

from urllib.parse import urlparse

import pytest

from apps.apis.models import APIExplorer, ResourceMethod
from apps.evergo.models import EvergoUser

EXPECTED_METHOD_PATHS = {
    ("POST", "/login"),
    ("GET", "/config/catalogs/sitios/all"),
    ("GET", "/ordenes/search-ingenieros"),
    ("GET", "/config/catalogs/orden-estatus"),
    ("GET", "/ordenes/instalador-coordinador"),
    ("GET", "/user"),
    ("GET", "/ordenes/{order_id}"),
    ("GET", "/ordenes/kit-cfe/{order_id}"),
    ("GET", "/config/catalogs/instaladores/empresas/coord-para-asignar"),
    ("GET", "/reportes/ordenes/{order_id}/visita-tecnica/cuestionario-preguntas"),
    ("GET", "/users/reassignment_reason"),
    ("GET", "/users/crew-people"),
}

EVERGO_MODEL_URLS = (
    EvergoUser.API_LOGIN_URL,
    EvergoUser.API_SITIOS_URL,
    EvergoUser.API_INGENIEROS_URL,
    EvergoUser.API_ORDEN_ESTATUS_URL,
    EvergoUser.API_ORDERS_URL,
)


@pytest.mark.django_db
def test_evergo_api_explorer_seeded_endpoints_regression() -> None:
    """Regression: migration should seed all Evergo integration endpoints."""

    api = APIExplorer.objects.get(name="Evergo API")
    methods = {
        (method.http_method, method.resource_path)
        for method in ResourceMethod.objects.filter(api=api)
    }

    assert api.base_url == "https://portal-backend.evergo.com/api/mex/v1/"
    assert methods == EXPECTED_METHOD_PATHS


@pytest.mark.django_db
def test_evergo_api_explorer_matches_model_endpoints_regression() -> None:
    """Regression: seeded API explorer routes should include all endpoint constants used by the model."""

    api = APIExplorer.objects.get(name="Evergo API")
    seeded_paths = {method.resource_path for method in ResourceMethod.objects.filter(api=api)}
    base_path = urlparse(api.base_url).path
    expected_paths = {
        f"/{urlparse(url).path.removeprefix(base_path).lstrip('/')}"
        for url in EVERGO_MODEL_URLS
    }

    assert expected_paths.issubset(seeded_paths)
