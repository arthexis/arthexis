"""Tests for Evergo profile synchronization behavior."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from django.contrib.auth import get_user_model

from apps.evergo.exceptions import EvergoAPIError
from apps.evergo.models import EvergoCustomer, EvergoOrder, EvergoOrderFieldValue, EvergoUser


@pytest.mark.django_db
@patch("apps.evergo.models.user.requests.Session")
def test_test_login_populates_remote_fields(mock_session_cls):
    """Evergo login should persist the expected profile fields from the API payload."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite", email="suite@example.com")
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="reginaldocts@evergo.com",
        evergo_password="top-secret",  # noqa: S106
    )

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": 58642,
        "name": "Reginaldo Gutiérrez",
        "email": "reginaldocts@evergo.com",
        "two_factor_secret": "s3cr3t",
        "two_factor_recovery_codes": "[\"code-a\",\"code-b\"]",
        "two_factor_confirmed_at": "2025-12-15T21:00:00.000000Z",
        "two_fa_enabled": 0,
        "two_fa_authenticated": 1,
        "created_at": "2025-12-11T18:18:48.000000Z",
        "updated_at": "2025-12-15T20:43:59.000000Z",
        "subempresas": [
            {
                "id": 25,
                "idInstalaEmpresa": 8,
                "nombre": "Reginaldo Gutiérrez",
            }
        ],
    }
    mock_session = mock_session_cls.return_value.__enter__.return_value
    mock_prime_response = Mock()
    mock_prime_response.raise_for_status.return_value = None
    mock_session.get.return_value = mock_prime_response
    mock_session.cookies.get.return_value = "mocked-xsrf-token"
    mock_session.post.return_value = mock_response

    result = profile.test_login()
    profile.refresh_from_db()

    assert result.response_code == 200
    assert profile.evergo_user_id == 58642
    assert profile.name == "Reginaldo Gutiérrez"
    assert profile.email == "reginaldocts@evergo.com"
    assert profile.empresa_id == 8
    assert profile.subempresa_id == 25
    assert profile.subempresa_name == "Reginaldo Gutiérrez"
    assert profile.two_fa_enabled is False
    assert profile.two_fa_authenticated is True
    assert profile.two_factor_secret == "s3cr3t"
    assert profile.two_factor_recovery_codes == '["code-a","code-b"]'
    assert profile.two_factor_confirmed_at is not None
    assert profile.evergo_created_at is not None
    assert profile.evergo_updated_at is not None
    assert profile.last_login_test_at is not None


@pytest.mark.django_db
@patch("apps.evergo.models.user.requests.Session")
def test_test_login_raises_specific_error_for_419(mock_session_cls):
    """Evergo login should surface a specific CSRF/session message when backend responds 419."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite-419", email="suite-419@example.com")
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="reginaldocts@evergo.com",
        evergo_password="top-secret",  # noqa: S106
    )

    mock_session = mock_session_cls.return_value.__enter__.return_value
    mock_prime_response = Mock()
    mock_prime_response.raise_for_status.return_value = None
    mock_session.get.return_value = mock_prime_response
    mock_session.cookies.get.return_value = "mocked-xsrf-token"

    mock_response = Mock()
    mock_response.status_code = 419
    mock_session.post.return_value = mock_response

    with pytest.raises(EvergoAPIError, match="status 419"):
        profile.test_login()


@pytest.mark.django_db
@patch("apps.evergo.models.user.requests.Session")
def test_load_orders_syncs_only_assigned_orders_and_catalog_values(mock_session_cls):
    """Load orders should upsert assigned orders and refresh learned dropdown field values."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite-orders", email="suite-orders@example.com")
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="reginaldocts@evergo.com",
        evergo_password="top-secret",  # noqa: S106
        evergo_user_id=58642,
    )

    mock_session = mock_session_cls.return_value.__enter__.return_value
    mock_prime_response = Mock()
    mock_prime_response.raise_for_status.return_value = None
    mock_session.get.return_value = mock_prime_response
    mock_session.cookies.get.return_value = "mocked-xsrf-token"

    def _response(payload):
        response = Mock()
        response.status_code = 200
        response.json.return_value = payload
        return response

    def request_side_effect(*, method, url, **kwargs):
        if url.endswith("/login"):
            return _response(
                {
                    "id": 58642,
                    "name": "Reginaldo Gutiérrez",
                    "email": "reginaldocts@evergo.com",
                    "subempresas": [],
                }
            )
        if "catalogs/sitios/all" in url:
            return _response([{"id": 36, "nombre": "Geely"}])
        if "search-ingenieros" in url:
            return _response(
                [
                    {
                        "id": 58642,
                        "name": "Reginaldo Gutiérrez",
                        "user_info": "Reginaldo Gutiérrez - reginaldocts@evergo.com",
                    }
                ]
            )
        if "catalogs/orden-estatus" in url:
            return _response([{"id": 8, "nombre": "Orden concluida"}])
        if "ordenes/instalador-coordinador" in url:
            return _response(
                {
                    "current_page": 1,
                    "last_page": 1,
                    "data": [
                        {
                            "id": 29759,
                            "numero_orden": "GLY01026",
                            "idSitio": 36,
                            "idCliente": 64473,
                            "prefijo": "GLY",
                            "uuid": 1026,
                            "has_vehicle": 1,
                            "has_charger": 1,
                            "idOrdenEstatus": 8,
                            "paymentBy": "Brand",
                            "user_tecnico_id": 58642,
                            "created_at": "2026-01-16T22:38:58.000000Z",
                            "updated_at": "2026-02-21T18:30:58.000000Z",
                            "sitio": {"id": 36, "nombre": "Geely"},
                            "estatus": {"id": 8, "nombre": "Orden concluida"},
                            "preorden_tipo": {"id": 107, "nombre": "Geely - Instalación"},
                            "cliente": {"id": 64473, "name": "ALMA ROSA AGUIRRE"},
                            "orden_instalador": {
                                "idIngeniero": 58642,
                                "idCoordinador": 58642,
                                "ingeniero": {"id": 58642, "name": "Reginaldo Gutiérrez"},
                                "coordinador": {"id": 58642, "name": "Reginaldo Gutiérrez"},
                            },
                            "cargadores": [{"id": 3553}],
                        },
                        {
                            "id": 39759,
                            "numero_orden": "GLY02001",
                            "user_tecnico_id": 99999,
                            "orden_instalador": {
                                "idIngeniero": 99999,
                                "idCoordinador": 99999,
                            },
                        },
                    ],
                }
            )
        raise AssertionError(f"Unexpected URL {url}")

    mock_session.request.side_effect = request_side_effect

    created, updated = profile.load_orders()

    assert created == 1
    assert updated == 0
    order = EvergoOrder.objects.get(remote_id=29759)
    assert order.order_number == "GLY01026"
    assert order.assigned_engineer_id == 58642
    assert order.charger_count == 1
    assert order.phone_primary == ""
    assert order.phone_secondary == ""

    assert not EvergoOrder.objects.filter(remote_id=39759).exists()
    assert EvergoOrderFieldValue.objects.filter(field_name="sitio", remote_id=36).exists()
    assert EvergoOrderFieldValue.objects.filter(field_name="estatus", remote_id=8).exists()
    assert EvergoOrderFieldValue.objects.filter(field_name="preorden_tipo", remote_id=107).exists()
    assert EvergoOrderFieldValue.objects.filter(field_name="payment_by", remote_name="Brand").exists()


@pytest.mark.django_db
@patch("apps.evergo.models.user.requests.Session")
def test_load_customers_from_queries_creates_customer_and_placeholder_order(mock_session_cls):
    """Regression: customer wizard should create customer rows and provisional SO placeholders."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite-customer", email="suite-customer@example.com")
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="reginaldocts@evergo.com",
        evergo_password="top-secret",  # noqa: S106
        evergo_user_id=58642,
    )

    mock_session = mock_session_cls.return_value.__enter__.return_value
    mock_prime_response = Mock()
    mock_prime_response.raise_for_status.return_value = None
    mock_session.get.return_value = mock_prime_response
    mock_session.cookies.get.return_value = "mocked-xsrf-token"

    def _response(payload):
        response = Mock()
        response.status_code = 200
        response.json.return_value = payload
        return response

    def request_side_effect(*, method, url, params=None, **kwargs):
        if url.endswith("/login"):
            return _response({"id": 58642, "name": "Reginaldo", "email": "reginaldocts@evergo.com"})
        if "ordenes/instalador-coordinador" in url:
            if params and params.get("numero") == "J00830":
                return _response(
                    {
                        "current_page": 1,
                        "last_page": 1,
                        "data": [
                            {
                                "id": 30161,
                                "numero_orden": "J00830",
                                "idCliente": 67883,
                                "user_tecnico_id": 58642,
                                "updated_at": "2026-02-23T20:00:33.000000Z",
                                "cliente": {
                                    "id": 67883,
                                    "name": "irma ravize",
                                    "email": "irma@notaria55mty.com",
                                },
                                "orden_instalacion": {
                                    "telefono_celular": "+528115889790",
                                    "direccion": "capellania 107 San Pedro Garza García",
                                    "nombre_completo": "irma ravize",
                                },
                            }
                        ],
                    }
                )
            if params and params.get("numero") == "BAD999":
                return _response({"current_page": 1, "last_page": 1, "data": []})
            if params and params.get("cliente") == "irma ravize":
                return _response({"current_page": 1, "last_page": 1, "data": []})
        raise AssertionError(f"Unexpected URL {url} params={params}")

    mock_session.request.side_effect = request_side_effect

    summary = profile.load_customers_from_queries(raw_queries="J00830; BAD999; irma ravize")

    assert summary["orders_created"] >= 1
    assert summary["placeholders_created"] == 1
    assert "BAD999" in summary["unresolved"]
    assert "irma ravize" in summary["unresolved"]

    customer = profile.customers.get(remote_id=67883)
    assert customer.latest_so == "J00830"
    assert customer.phone_number == "+528115889790"

    placeholder = EvergoOrder.objects.get(order_number="BAD999")
    assert placeholder.validation_state == EvergoOrder.VALIDATION_STATE_PLACEHOLDER


@pytest.mark.django_db
def test_upsert_order_extracts_contact_and_address_components():
    """Regression: order sync should persist phone and address pieces for admin usage."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite-upsert", email="suite-upsert@example.com")
    profile = EvergoUser.objects.create(user=suite_user)

    payload = {
        "id": 29545,
        "numero_orden": "GM01321",
        "idSitio": 25,
        "sitio": {"id": 25, "nombre": "Chevrolet"},
        "idCliente": 63100,
        "cliente": {"id": 63100, "name": "JESUS ALBERTO CORTEZ HARO"},
        "orden_instalacion": {
            "telefono_celular": "+528111852788",
            "telefono_fijo1": "81 7770 0000",
            "telefono_fijo2": "81 7770 1111",
            "calle": "santa barbara",
            "num_ext": "404",
            "num_int": "2B",
            "entre_calles": "A y B",
            "colonia": "Fuentes de Santa Lucia",
            "municipio": "Apodaca",
            "ciudad": "Ciudad Apodaca",
            "codigo_postal": "66647",
        },
        "created_at": "2026-01-08T22:06:58.000000Z",
        "updated_at": "2026-01-13T02:18:42.000000Z",
    }

    created = profile._upsert_order(payload)

    assert created is True
    order = EvergoOrder.objects.get(remote_id=29545)
    assert order.site_name == "Chevrolet"
    assert order.phone_primary == "+528111852788"
    assert order.phone_secondary == "81 7770 0000"
    assert order.address_street == "santa barbara"
    assert order.address_num_ext == "404"
    assert order.address_num_int == "2B"
    assert order.address_between_streets == "A y B"
    assert order.address_neighborhood == "Fuentes de Santa Lucia"
    assert order.address_municipality == "Apodaca"
    assert order.address_city == "Ciudad Apodaca"
    assert order.address_postal_code == "66647"


@pytest.mark.django_db
def test_upsert_customer_ignores_blank_municipio_and_falls_back_to_ciudad():
    """Regression: whitespace municipio should not block a valid ciudad fallback in address composition."""
    user_model = get_user_model()
    suite_user = user_model.objects.create_user(
        username="suite-customer-locality-blank-municipio",
        email="suite-customer-locality-blank-municipio@example.com",
    )
    profile = EvergoUser.objects.create(user=suite_user)

    payload = {
        "id": 43121,
        "numero_orden": "SO-43121",
        "updated_at": "2026-01-13T02:18:42.000000Z",
        "cliente": {"id": 11002, "name": "Ciudad Fallback", "email": "ciudad@example.com"},
        "orden_instalacion": {
            "calle": "santa barbara",
            "num_ext": "404",
            "colonia": "Fuentes de Santa Lucia",
            "municipio": "   ",
            "ciudad": "Monterrey",
            "codigo_postal": "64000",
        },
    }

    created = profile._upsert_customer_from_order(payload)

    assert created is True
    customer = EvergoCustomer.objects.get(user=profile, remote_id=11002)
    assert "Monterrey" in customer.address


@pytest.mark.django_db
def test_upsert_customer_prefers_municipio_over_ciudad_in_computed_address():
    """Regression: customer address fallback should avoid municipio/ciudad duplication by preferring municipio."""
    user_model = get_user_model()
    suite_user = user_model.objects.create_user(username="suite-customer-locality", email="suite-customer-locality@example.com")
    profile = EvergoUser.objects.create(user=suite_user)

    payload = {
        "id": 43120,
        "numero_orden": "SO-43120",
        "updated_at": "2026-01-13T02:18:42.000000Z",
        "cliente": {"id": 11001, "name": "Municipio First", "email": "municipio@example.com"},
        "orden_instalacion": {
            "calle": "santa barbara",
            "num_ext": "404",
            "colonia": "Fuentes de Santa Lucia",
            "municipio": "Apodaca",
            "ciudad": "Ciudad Apodaca",
            "codigo_postal": "66647",
        },
    }

    created = profile._upsert_customer_from_order(payload)

    assert created is True
    customer = EvergoCustomer.objects.get(user=profile, remote_id=11001)
    assert "Apodaca" in customer.address
    assert "Ciudad Apodaca" not in customer.address
