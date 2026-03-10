"""Admin regression tests for suite feature workflows."""

import pytest

from apps.features.admin import FeatureAdminForm
from apps.features.models import Feature


def test_ocpp_simulator_form_rejects_disabling_all_backends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OCPP simulator admin form should require at least one backend enabled."""

    monkeypatch.setattr(Feature, "validate_unique", lambda self, exclude=None: None)
    feature = Feature(slug="ocpp-simulator", display="OCPP Simulator")
    form = FeatureAdminForm(
        instance=feature,
        data={
            "slug": "ocpp-simulator",
            "display": "OCPP Simulator",
            "summary": "",
            "is_enabled": "on",
            "admin_requirements": "",
            "public_requirements": "",
            "service_requirements": "",
            "admin_views": "[]",
            "public_views": "[]",
            "service_views": "[]",
            "code_locations": "[]",
            "protocol_coverage": "{}",
            "metadata": "{}",
            "param__arthexis_backend": "disabled",
            "param__mobilityhouse_backend": "disabled",
        },
    )

    assert form.is_valid() is False
    assert "param__arthexis_backend" in form.errors
    assert "param__mobilityhouse_backend" in form.errors


def test_ocpp_simulator_form_accepts_when_one_backend_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OCPP simulator admin form should allow settings with one enabled backend."""

    monkeypatch.setattr(Feature, "validate_unique", lambda self, exclude=None: None)
    feature = Feature(slug="ocpp-simulator", display="OCPP Simulator")
    form = FeatureAdminForm(
        instance=feature,
        data={
            "slug": "ocpp-simulator",
            "display": "OCPP Simulator",
            "summary": "",
            "is_enabled": "on",
            "admin_requirements": "",
            "public_requirements": "",
            "service_requirements": "",
            "admin_views": "[]",
            "public_views": "[]",
            "service_views": "[]",
            "code_locations": "[]",
            "protocol_coverage": "{}",
            "metadata": "{}",
            "param__arthexis_backend": "enabled",
            "param__mobilityhouse_backend": "disabled",
        },
    )

    assert form.is_valid() is True
