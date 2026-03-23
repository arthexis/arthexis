"""Tests for Evergo profile synchronization behavior."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from apps.evergo.exceptions import EvergoAPIError
from apps.evergo.models import (
    EvergoCustomer,
    EvergoOrder,
    EvergoOrderFieldValue,
    EvergoUser,
)


@pytest.mark.django_db
def test_evergo_user_rejects_empty_email_at_database_level():
    """Database constraint should reject direct saves with an empty Evergo email."""
    User = get_user_model()
    suite_user = User.objects.create_user(
        username="suite-empty", email="suite-empty@example.com"
    )

    with pytest.raises(IntegrityError, match="evergo_evergouser_email_non_empty"):
        EvergoUser.objects.create(user=suite_user, evergo_email="")


@pytest.mark.django_db
def test_test_login_populates_remote_fields(monkeypatch):
    """Evergo login should persist the expected profile fields from the API payload."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite", email="suite@example.com")
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="reginaldocts@evergo.com",
        evergo_password="top-secret",  # noqa: S106
    )

    response_payload = {
        "id": 58642,
        "name": "Reginaldo Gutiérrez",
        "email": "reginaldocts@evergo.com",
        "two_factor_secret": "s3cr3t",
        "two_factor_recovery_codes": '["code-a","code-b"]',
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
    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return response_payload

    monkeypatch.setattr(EvergoUser, "_prime_session", lambda self, **_: "token")
    monkeypatch.setattr(
        "apps.evergo.models.user.requests.Session",
        lambda: Mock(__enter__=Mock(return_value=Mock(post=Mock(return_value=_FakeResponse()))), __exit__=Mock(return_value=False)),
    )

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
def test_test_login_raises_specific_error_for_419(monkeypatch):
    """Evergo login should surface a specific CSRF/session message when backend responds 419."""
    User = get_user_model()
    suite_user = User.objects.create_user(
        username="suite-419", email="suite-419@example.com"
    )
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="reginaldocts@evergo.com",
        evergo_password="top-secret",  # noqa: S106
    )

    class _FakeResponse:
        status_code = 419

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(EvergoUser, "_prime_session", lambda self, **_: "token")
    monkeypatch.setattr(
        "apps.evergo.models.user.requests.Session",
        lambda: Mock(__enter__=Mock(return_value=Mock(post=Mock(return_value=_FakeResponse()))), __exit__=Mock(return_value=False)),
    )

    with pytest.raises(EvergoAPIError, match="status 419"):
        profile.test_login()


@pytest.mark.django_db
def test_load_orders_syncs_only_assigned_orders_and_catalog_values(monkeypatch):
    """Load orders should upsert assigned orders and refresh learned dropdown field values."""
    User = get_user_model()
    suite_user = User.objects.create_user(
        username="suite-orders", email="suite-orders@example.com"
    )
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="reginaldocts@evergo.com",
        evergo_password="top-secret",  # noqa: S106
        evergo_user_id=58642,
    )

    def request_side_effect(self, *, session, timeout, method, url, **kwargs):
        if url.endswith("/login"):
            return (
                {
                    "id": 58642,
                    "name": "Reginaldo Gutiérrez",
                    "email": "reginaldocts@evergo.com",
                    "subempresas": [],
                }
            )
        if "catalogs/sitios/all" in url:
            return [{"id": 36, "nombre": "Geely"}]
        if "search-ingenieros" in url:
            return (
                [
                    {
                        "id": 58642,
                        "name": "Reginaldo Gutiérrez",
                        "user_info": "Reginaldo Gutiérrez - reginaldocts@evergo.com",
                    }
                ]
            )
        if "catalogs/orden-estatus" in url:
            return [{"id": 8, "nombre": "Orden concluida"}]
        if "ordenes/instalador-coordinador" in url:
            return (
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
                            "preorden_tipo": {
                                "id": 107,
                                "nombre": "Geely - Instalación",
                            },
                            "cliente": {"id": 64473, "name": "ALMA ROSA AGUIRRE"},
                            "orden_instalador": {
                                "idIngeniero": 58642,
                                "idCoordinador": 58642,
                                "ingeniero": {
                                    "id": 58642,
                                    "name": "Reginaldo Gutiérrez",
                                },
                                "coordinador": {
                                    "id": 58642,
                                    "name": "Reginaldo Gutiérrez",
                                },
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
    monkeypatch.setattr(EvergoUser, "_login_session", lambda self, **_: None)
    monkeypatch.setattr(EvergoUser, "_request_json", request_side_effect)

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
    assert EvergoOrderFieldValue.objects.filter(
        field_name="sitio", remote_id=36
    ).exists()
    assert EvergoOrderFieldValue.objects.filter(
        field_name="estatus", remote_id=8
    ).exists()
    assert EvergoOrderFieldValue.objects.filter(
        field_name="preorden_tipo", remote_id=107
    ).exists()
    assert EvergoOrderFieldValue.objects.filter(
        field_name="payment_by", remote_name="Brand"
    ).exists()


@pytest.mark.django_db
def test_load_customers_from_queries_creates_customer_and_placeholder_order(
    monkeypatch,
):
    """Regression: customer wizard should create customer rows and provisional SO placeholders."""
    User = get_user_model()
    suite_user = User.objects.create_user(
        username="suite-customer", email="suite-customer@example.com"
    )
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="reginaldocts@evergo.com",
        evergo_password="top-secret",  # noqa: S106
        evergo_user_id=58642,
    )

    def request_side_effect(self, *, session, timeout, method, url, params=None, **kwargs):
        if url.endswith("/login"):
            return (
                {"id": 58642, "name": "Reginaldo", "email": "reginaldocts@evergo.com"}
            )
        if "ordenes/instalador-coordinador" in url:
            if params and params.get("numero") == "J00830":
                return (
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
                return {"current_page": 1, "last_page": 1, "data": []}
            if params and params.get("cliente") == "irma ravize":
                return {"current_page": 1, "last_page": 1, "data": []}
        raise AssertionError(f"Unexpected URL {url} params={params}")
    monkeypatch.setattr(EvergoUser, "_login_session", lambda self, **_: None)
    monkeypatch.setattr(EvergoUser, "_request_json", request_side_effect)

    summary = profile.load_customers_from_queries(
        raw_queries="J00830; BAD999; irma ravize"
    )

    assert summary["orders_created"] >= 1
    assert summary["placeholders_created"] == 1
    assert "BAD999" in summary["unresolved"]
    assert "irma ravize" in summary["unresolved"]

    customer = profile.customers.get(remote_id=67883)
    assert customer.latest_so == "J00830"
    assert customer.phone_number == "+528115889790"

    synced_order = profile.orders.get(remote_id=30161)
    assert summary["loaded_order_ids"] == [synced_order.pk]
    assert summary["loaded_customer_ids"] == [customer.pk]

    placeholder = EvergoOrder.objects.get(order_number="BAD999")
    assert placeholder.validation_state == EvergoOrder.VALIDATION_STATE_PLACEHOLDER


@pytest.mark.django_db
def test_upsert_order_extracts_contact_and_address_components():
    """Regression: order sync should persist phone and address pieces for admin usage."""
    User = get_user_model()
    suite_user = User.objects.create_user(
        username="suite-upsert", email="suite-upsert@example.com"
    )
    profile = EvergoUser.objects.create(
        user=suite_user, evergo_email="suite-upsert@evergo.example.com"
    )

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

    created, order = profile._upsert_order(payload)

    assert created is True
    assert order.remote_id == 29545
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
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="suite-customer-locality-blank-municipio@evergo.example.com",
    )

    payload = {
        "id": 43121,
        "numero_orden": "SO-43121",
        "updated_at": "2026-01-13T02:18:42.000000Z",
        "cliente": {
            "id": 11002,
            "name": "Ciudad Fallback",
            "email": "ciudad@example.com",
        },
        "orden_instalacion": {
            "calle": "santa barbara",
            "num_ext": "404",
            "colonia": "Fuentes de Santa Lucia",
            "municipio": "   ",
            "ciudad": "Monterrey",
            "codigo_postal": "64000",
        },
    }

    created, customer = profile._upsert_customer_from_order(payload)

    assert created is True
    assert customer is not None
    customer = EvergoCustomer.objects.get(user=profile, remote_id=11002)
    assert "Monterrey" in customer.address


@pytest.mark.django_db
def test_upsert_customer_prefers_municipio_over_ciudad_in_computed_address():
    """Regression: customer address fallback should avoid municipio/ciudad duplication by preferring municipio."""
    user_model = get_user_model()
    suite_user = user_model.objects.create_user(
        username="suite-customer-locality", email="suite-customer-locality@example.com"
    )
    profile = EvergoUser.objects.create(
        user=suite_user, evergo_email="suite-customer-locality@evergo.example.com"
    )

    payload = {
        "id": 43120,
        "numero_orden": "SO-43120",
        "updated_at": "2026-01-13T02:18:42.000000Z",
        "cliente": {
            "id": 11001,
            "name": "Municipio First",
            "email": "municipio@example.com",
        },
        "orden_instalacion": {
            "calle": "santa barbara",
            "num_ext": "404",
            "colonia": "Fuentes de Santa Lucia",
            "municipio": "Apodaca",
            "ciudad": "Ciudad Apodaca",
            "codigo_postal": "66647",
        },
    }

    created, customer = profile._upsert_customer_from_order(payload)

    assert created is True
    assert customer is not None
    customer = EvergoCustomer.objects.get(user=profile, remote_id=11001)
    assert "Apodaca" in customer.address
    assert "Ciudad Apodaca" not in customer.address


@pytest.mark.django_db
def test_load_customers_from_queries_without_filters_uses_access_scope(
    monkeypatch,
):
    """Regression: empty query should request all accessible customers for the engineer profile."""
    user_model = get_user_model()
    suite_user = user_model.objects.create_user(
        username="suite-load-all", email="suite-load-all@example.com"
    )
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="load-all@evergo.example.com",
        evergo_password="top-secret",  # noqa: S106
        evergo_user_id=58642,
    )

    def request_side_effect(self, *, session, timeout, method, url, params=None, **kwargs):
        if url.endswith("/login"):
            return (
                {
                    "id": 58642,
                    "name": "Load All User",
                    "email": "load-all@evergo.example.com",
                }
            )
        if "ordenes/instalador-coordinador" in url:
            assert params is not None
            assert params.get("numero") == ""
            assert params.get("cliente") == ""
            return (
                {
                    "current_page": 1,
                    "last_page": 1,
                    "data": [
                        {
                            "id": 501,
                            "numero_orden": "AA501",
                            "idCliente": 9001,
                            "user_tecnico_id": 58642,
                            "cliente": {
                                "id": 9001,
                                "name": "All Scope Customer",
                                "email": "all@example.com",
                            },
                        }
                    ],
                }
            )
        raise AssertionError(f"Unexpected URL {url} params={params}")
    monkeypatch.setattr(EvergoUser, "_login_session", lambda self, **_: None)
    monkeypatch.setattr(EvergoUser, "_request_json", request_side_effect)

    summary = profile.load_customers_from_queries(raw_queries="")

    assert summary["customer_names"] == []
    assert summary["customers_loaded"] == 1
    assert summary["unresolved"] == []
    assert profile.customers.filter(remote_id=9001, name="All Scope Customer").exists()


@pytest.mark.django_db
def test_reload_customer_from_remote_rebuilds_customer_and_order(
    monkeypatch,
):
    """Regression: reloading one customer should delete stale snapshots and recreate from remote payload."""
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-reload-customer", email="suite-reload-customer@example.com"
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-reload-customer@evergo.example.com",
        evergo_password="top-secret",  # noqa: S106
        evergo_user_id=58642,
    )
    stale_order = EvergoOrder.objects.create(
        user=profile,
        remote_id=777,
        order_number="GLY01228",
        status_name="Old Status",
        raw_payload={"stale": True},
    )
    stale_customer = EvergoCustomer.objects.create(
        user=profile,
        remote_id=901,
        name="Old Customer",
        latest_so="GLY01228",
        latest_order=stale_order,
        raw_payload={"stale": True},
    )

    monkeypatch.setattr(
        EvergoUser,
        "fetch_order_detail",
        lambda self, **_: {
        "id": 777,
        "numero_orden": "GLY01228",
        "updated_at": "2026-01-13T02:18:42.000000Z",
        "estatus": {"nombre": "En Proceso"},
        "cliente": {"id": 901, "name": "Fresh Customer", "email": "fresh@example.com"},
        "orden_instalacion": {
            "municipio": "Apodaca",
            "calle": "Nueva",
            "num_ext": "15",
        },
        },
    )

    stale_customer_pk = stale_customer.pk
    stale_order_pk = stale_order.pk

    refreshed = profile.reload_customer_from_remote(customer=stale_customer)

    assert refreshed.name == "Fresh Customer"
    assert refreshed.pk != stale_customer_pk
    refreshed_order = EvergoOrder.objects.get(user=profile, remote_id=777)
    assert refreshed_order.status_name == "En Proceso"
    assert refreshed_order.pk != stale_order_pk
    assert not EvergoOrder.objects.filter(pk=stale_order_pk).exists()
    assert not EvergoCustomer.objects.filter(
        pk=stale_customer_pk, name="Old Customer"
    ).exists()


@pytest.mark.django_db
def test_reload_customer_from_remote_uses_name_lookup_when_latest_order_missing(
    monkeypatch,
):
    """Fallback reload should locate refreshed customer by original name when no latest order exists."""
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-reload-customer-name",
        email="suite-reload-customer-name@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-reload-customer-name@evergo.example.com",
        evergo_password="top-secret",  # noqa: S106
        evergo_user_id=58642,
    )
    stale_customer = EvergoCustomer.objects.create(
        user=profile,
        remote_id=1901,
        name="Customer Name Match",
        latest_so="GLY12345",
        latest_order=None,
    )

    def load_side_effect(self, *, raw_queries, timeout=20):
        assert "GLY12345" in raw_queries
        EvergoCustomer.objects.create(
            user=self,
            remote_id=2901,
            name="Customer Name Match",
            latest_so="GLY12345",
        )
        return {"customers_loaded": 1}

    monkeypatch.setattr(EvergoUser, "load_customers_from_queries", load_side_effect)

    refreshed = profile.reload_customer_from_remote(customer=stale_customer)

    assert refreshed.pk != stale_customer.pk
    assert refreshed.name == "Customer Name Match"


@pytest.mark.django_db
def test_reload_customer_from_remote_renames_stale_snapshot_before_name_fallback(
    monkeypatch,
):
    """Fallback reload should prevent the stale row from being reused by name-only upserts."""
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-reload-customer-name-stale",
        email="suite-reload-customer-name-stale@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-reload-customer-name-stale@evergo.example.com",
        evergo_password="top-secret",  # noqa: S106
        evergo_user_id=58642,
    )
    stale_customer = EvergoCustomer.objects.create(
        user=profile,
        remote_id=None,
        name="Customer Name Match",
        latest_so="GLY12345",
        latest_order=None,
    )

    def load_side_effect(self, *, raw_queries, timeout=20):
        assert "GLY12345" in raw_queries
        self._upsert_customer_from_order(
            {
                "id": 87001,
                "numero_orden": "GLY12345",
                "updated_at": "2026-01-13T02:18:42.000000Z",
                "cliente": {"name": "Customer Name Match"},
                "orden_instalacion": {"nombre_completo": "Customer Name Match"},
            }
        )
        return {"customers_loaded": 0}

    monkeypatch.setattr(EvergoUser, "load_customers_from_queries", load_side_effect)

    refreshed = profile.reload_customer_from_remote(customer=stale_customer)

    assert refreshed.pk != stale_customer.pk
    assert refreshed.name == "Customer Name Match"
    assert not EvergoCustomer.objects.filter(pk=stale_customer.pk).exists()


@pytest.mark.django_db
def test_reload_customer_from_remote_rolls_back_on_reload_failure(monkeypatch):
    """Customer snapshot should remain if remote fallback reload fails."""
    user_model = get_user_model()
    owner = user_model.objects.create_user(
        username="suite-reload-customer-fail",
        email="suite-reload-customer-fail@example.com",
    )
    profile = EvergoUser.objects.create(
        user=owner,
        evergo_email="suite-reload-customer-fail@evergo.example.com",
        evergo_password="top-secret",  # noqa: S106
        evergo_user_id=58642,
    )
    stale_customer = EvergoCustomer.objects.create(
        user=profile,
        remote_id=3901,
        name="Persistent Customer",
        latest_so="GLY54321",
        latest_order=None,
    )
    monkeypatch.setattr(
        EvergoUser,
        "load_customers_from_queries",
        lambda self, **_: {"customers_loaded": 0},
    )

    stale_pk = stale_customer.pk

    with pytest.raises(EvergoAPIError, match="did not return data"):
        profile.reload_customer_from_remote(customer=stale_customer)

    assert stale_pk is not None
    assert EvergoCustomer.objects.filter(pk=stale_pk).exists()


@pytest.mark.django_db
def test_submit_tracking_phase_one_skips_visita_request_when_incomplete(
    monkeypatch,
):
    """Regression: visita endpoint must not be called when visita completion is False."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite-phase-one", email="suite-phase-one@example.com")
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="suite-phase-one@example.com",
        evergo_password="secret",
        evergo_user_id=58642,
    )

    calls: list[str] = []

    def _fake_request_json(self, *, session, timeout, method, url, **kwargs):
        return {}

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *args, **kwargs):
            calls.append("post")
            response = Mock()
            response.status_code = 200
            response.content = b"{}"
            response.json.return_value = {}
            return response

    monkeypatch.setattr(EvergoUser, "_login_session", lambda self, **_: None)
    monkeypatch.setattr(EvergoUser, "_request_json", _fake_request_json)
    monkeypatch.setattr("apps.evergo.models.user.requests.Session", _FakeSession)

    result = profile.submit_tracking_phase_one(
        order_id=30316,
        payload={"fecha_visita": "2026-03-10 10:00:00"},
        files={},
        step_completion={"visita": False, "assign": False, "install": False, "montage": False},
    )

    assert calls == []
    assert result["phase_1_status"] is None
    assert result["phase_1_payload"] == {}
    assert result["completed_steps"] == 0


@pytest.mark.django_db
def test_submit_tracking_phase_one_handles_non_json_payload_responses():
    """Regression: non-JSON response bodies should not crash payload extraction."""
    User = get_user_model()
    suite_user = User.objects.create_user(username="suite-phase-json", email="suite-phase-json@example.com")
    profile = EvergoUser.objects.create(
        user=suite_user,
        evergo_email="suite-phase-json@example.com",
        evergo_password="secret",
        evergo_user_id=58642,
    )

    broken_response = Mock()
    broken_response.content = b"<html>error</html>"
    broken_response.json.side_effect = ValueError("invalid json")

    assert profile._safe_json_extract(broken_response) == {}
