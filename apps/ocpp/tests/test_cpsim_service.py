"""Regression tests for OCPP simulator suite feature toggles."""

import pytest
from django.urls import reverse

from apps.features.models import Feature
from apps.ocpp.cpsim_service import cpsim_service_enabled, get_cpsim_feature


@pytest.mark.django_db
def test_cpsim_service_enabled_uses_suite_feature_state():
    """cpsim_service_enabled should read state from the suite feature table."""

    Feature.objects.update_or_create(
        slug="cpsim-service",
        defaults={"display": "OCPP Simulator", "is_enabled": False},
    )
    assert cpsim_service_enabled() is False

    Feature.objects.filter(slug="cpsim-service").update(is_enabled=True)
    assert cpsim_service_enabled() is True


@pytest.mark.django_db
def test_get_cpsim_feature_returns_suite_feature():
    """get_cpsim_feature should return the persisted suite feature row."""

    feature, _ = Feature.objects.update_or_create(
        slug="cpsim-service",
        defaults={"display": "OCPP Simulator", "is_enabled": True},
    )

    fetched = get_cpsim_feature()

    assert fetched is not None
    assert fetched.pk == feature.pk


@pytest.mark.django_db
def test_admin_toggle_cpsim_service_switches_suite_feature(admin_client, monkeypatch):
    """Simulator admin toggle should update the OCPP Simulator suite feature."""

    feature, _ = Feature.objects.update_or_create(
        slug="cpsim-service",
        defaults={"display": "OCPP Simulator", "is_enabled": False},
    )

    calls: list[bool] = []

    def _fake_queue(*, enabled, source=None, base_dir=None):
        calls.append(enabled)

    monkeypatch.setattr(
        "apps.ocpp.admin.miscellaneous.simulator_admin.queue_cpsim_service_toggle",
        _fake_queue,
    )

    response = admin_client.post(reverse("admin:ocpp_simulator_cpsim_toggle"))

    assert response.status_code == 302
    feature.refresh_from_db()
    assert feature.is_enabled is True
    assert calls == [True]
