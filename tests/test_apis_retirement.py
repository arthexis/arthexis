"""Regression coverage for retiring the legacy APIs token app."""

import pytest

from utils.role_app_profiles import (
    FEATURE_PACK_APP_SELECTORS,
    RETIRED_RUNTIME_APP_SELECTORS,
    explain_role_app_selectors,
    resolve_role_app_selectors,
)


def test_apis_app_cannot_be_selected_at_runtime():
    assert "apps.apis" in RETIRED_RUNTIME_APP_SELECTORS
    assert "api_service_tokens" not in FEATURE_PACK_APP_SELECTORS
    assert "apps.apis" not in resolve_role_app_selectors("control")

    result = explain_role_app_selectors(
        "control",
        explicit_apps=("apps.apis",),
        required_apps={},
    )

    assert "apps.apis" not in result.selectors


def test_retired_api_service_tokens_feature_pack_is_rejected():
    with pytest.raises(ValueError, match="Unknown feature pack"):
        resolve_role_app_selectors(
            "satellite",
            feature_packs=("api_service_tokens",),
        )
