"""Regression tests for simulator backend selection overrides."""

import pytest

from apps.simulators import simulator_runtime


def _mock_feature_parameters(values: dict[str, str]):
    def _get_feature_parameter(_slug: str, key: str, *, fallback: str = "") -> str:
        return values.get(key, fallback)

    return _get_feature_parameter


def test_backend_override_uses_mobilityhouse_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting Mobility House should use v2 when feature and dependency are present."""

    monkeypatch.setattr(
        simulator_runtime,
        "get_feature_parameter",
        _mock_feature_parameters(
            {
                simulator_runtime.ARTHEXIS_BACKEND_PARAMETER_KEY: "enabled",
                simulator_runtime.MOBILITY_HOUSE_BACKEND_PARAMETER_KEY: "enabled",
            }
        ),
    )
    monkeypatch.setattr(simulator_runtime, "find_spec", lambda _: object())

    selection = simulator_runtime.resolve_simulator_backend(preferred_backend="mobilityhouse")

    assert selection.use_mobility_house is True
    assert selection.backend == "mobility_house"


def test_backend_override_falls_back_when_mobilityhouse_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting Mobility House should fall back when optional dependency is absent."""

    monkeypatch.setattr(
        simulator_runtime,
        "get_feature_parameter",
        _mock_feature_parameters(
            {
                simulator_runtime.ARTHEXIS_BACKEND_PARAMETER_KEY: "enabled",
                simulator_runtime.MOBILITY_HOUSE_BACKEND_PARAMETER_KEY: "enabled",
            }
        ),
    )
    monkeypatch.setattr(simulator_runtime, "find_spec", lambda _: None)

    selection = simulator_runtime.resolve_simulator_backend(preferred_backend="mobilityhouse")

    assert selection.use_mobility_house is False
    assert selection.backend == "legacy"
    assert "legacy" in selection.reason.lower()


def test_backend_selection_reports_disabled_when_all_backends_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selection should report disabled state when both backend parameters are disabled."""

    monkeypatch.setattr(
        simulator_runtime,
        "get_feature_parameter",
        _mock_feature_parameters(
            {
                simulator_runtime.ARTHEXIS_BACKEND_PARAMETER_KEY: "disabled",
                simulator_runtime.MOBILITY_HOUSE_BACKEND_PARAMETER_KEY: "disabled",
            }
        ),
    )
    monkeypatch.setattr(simulator_runtime, "find_spec", lambda _: object())

    selection = simulator_runtime.resolve_simulator_backend()

    assert selection.use_mobility_house is False
    assert selection.backend == "legacy"
    assert "disabled" in selection.reason.lower()


def test_backend_selection_reports_unavailable_dependency_when_only_mobility_house_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selection should explain when Mobility House is enabled but ocpp is unavailable."""

    monkeypatch.setattr(
        simulator_runtime,
        "get_feature_parameter",
        _mock_feature_parameters(
            {
                simulator_runtime.ARTHEXIS_BACKEND_PARAMETER_KEY: "disabled",
                simulator_runtime.MOBILITY_HOUSE_BACKEND_PARAMETER_KEY: "enabled",
            }
        ),
    )
    monkeypatch.setattr(simulator_runtime, "find_spec", lambda _: None)

    selection = simulator_runtime.resolve_simulator_backend()

    assert selection.use_mobility_house is False
    assert selection.backend == "legacy"
    assert selection.feature_enabled is False
    assert selection.dependency_available is False
    assert "not installed" in selection.reason.lower()


def test_backend_selection_flags_backend_available_when_arthexis_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selection should keep feature_enabled true when Arthexis fallback is usable."""

    monkeypatch.setattr(
        simulator_runtime,
        "get_feature_parameter",
        _mock_feature_parameters(
            {
                simulator_runtime.ARTHEXIS_BACKEND_PARAMETER_KEY: "enabled",
                simulator_runtime.MOBILITY_HOUSE_BACKEND_PARAMETER_KEY: "disabled",
            }
        ),
    )
    monkeypatch.setattr(simulator_runtime, "find_spec", lambda _: object())

    selection = simulator_runtime.resolve_simulator_backend()

    assert selection.use_mobility_house is False
    assert selection.backend == "legacy"
    assert selection.feature_enabled is True

