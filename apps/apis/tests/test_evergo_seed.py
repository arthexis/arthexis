"""Regression tests for Evergo API explorer seeded endpoints."""

from urllib.parse import urlparse

import pytest
from django.core.management import call_command

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


def _ensure_evergo_api_seeded() -> APIExplorer:
    """Ensure Evergo API explorer data exists even when migrations are skipped."""

    api, _ = APIExplorer.objects.get_or_create(
        name="Evergo API",
        defaults={"base_url": "https://portal-backend.evergo.com/api/mex/v1/"},
    )
    call_command("loaddata", "apps/apis/fixtures/apis__evergo_endpoints.json", verbosity=0)
    return api


@pytest.mark.django_db
def test_evergo_api_explorer_seeded_endpoints() -> None:
    """Regression: migration should seed all Evergo integration endpoints."""

    api = _ensure_evergo_api_seeded()
    methods = {
        (method.http_method, method.resource_path)
        for method in ResourceMethod.objects.filter(api=api)
    }

    assert api.base_url == "https://portal-backend.evergo.com/api/mex/v1/"
    assert methods == EXPECTED_METHOD_PATHS


@pytest.mark.django_db
def test_evergo_api_explorer_matches_model_endpoints() -> None:
    """Regression: seeded API explorer routes should include all endpoint constants used by the model."""

    api = _ensure_evergo_api_seeded()
    seeded_paths = {method.resource_path for method in ResourceMethod.objects.filter(api=api)}
    base_path = urlparse(api.base_url).path
    model_urls = {
        value
        for attr, value in vars(EvergoUser).items()
        if attr.startswith("API_") and attr.endswith("_URL") and isinstance(value, str)
    }
    expected_paths = {
        f"/{urlparse(url).path.removeprefix(base_path).lstrip('/')}"
        for url in model_urls
    }

    assert expected_paths.issubset(seeded_paths)


@pytest.mark.django_db
def test_evergo_fixture_loaddata_is_idempotent() -> None:
    """Regression: loading Evergo endpoint fixture should update seeded rows without integrity errors."""

    api = _ensure_evergo_api_seeded()
    initial_count = ResourceMethod.objects.filter(api=api).count()

    call_command("loaddata", "apps/apis/fixtures/apis__evergo_endpoints.json", verbosity=0)

    assert ResourceMethod.objects.filter(api=api).count() == initial_count
