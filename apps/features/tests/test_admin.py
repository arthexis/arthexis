"""Admin regression tests for suite feature workflows."""

import pytest

from apps.features.admin import FeatureAdminForm
from apps.features.models import Feature


@pytest.mark.parametrize(
    ("arthexis_backend", "mobilityhouse_backend", "is_valid"),
    [
        ("disabled", "disabled", False),
        ("enabled", "disabled", True),
        ("disabled", "enabled", True),
        ("enabled", "enabled", True),
    ],
)
def test_ocpp_simulator_form_backend_validation(
    monkeypatch: pytest.MonkeyPatch,
    arthexis_backend: str,
    mobilityhouse_backend: str,
    is_valid: bool,
) -> None:
    """OCPP simulator admin form should validate backend availability."""

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
            "param__arthexis_backend": arthexis_backend,
            "param__mobilityhouse_backend": mobilityhouse_backend,
        },
    )

    assert form.is_valid() is is_valid
    if not is_valid:
        assert "param__arthexis_backend" in form.errors
        assert "param__mobilityhouse_backend" in form.errors
