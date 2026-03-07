"""Regression tests for simulator backend selection overrides."""

import pytest

from apps.simulators import simulator_runtime


@pytest.mark.parametrize("alias", ["arthexis", "legacy"])
def test_backend_override_forces_arthexis(alias: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Selecting the Arthexis backend should force the legacy runtime path."""

    monkeypatch.setattr(simulator_runtime, "get_feature_parameter", lambda *args, **kwargs: "enabled")
    monkeypatch.setattr(simulator_runtime, "find_spec", lambda _: object())

    selection = simulator_runtime.resolve_simulator_backend(preferred_backend=alias)

    assert selection.use_mobility_house is False
    assert selection.backend == "legacy"


def test_backend_override_uses_mobilityhouse_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting Mobility House should use v2 when feature and dependency are present."""

    monkeypatch.setattr(simulator_runtime, "get_feature_parameter", lambda *args, **kwargs: "enabled")
    monkeypatch.setattr(simulator_runtime, "find_spec", lambda _: object())

    selection = simulator_runtime.resolve_simulator_backend(preferred_backend="mobilityhouse")

    assert selection.use_mobility_house is True
    assert selection.backend == "mobility_house"


def test_backend_override_falls_back_when_mobilityhouse_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting Mobility House should fall back when optional dependency is absent."""

    def _param(_slug: str, key: str, *, fallback: str = "") -> str:
        del fallback
        return "enabled" if key == simulator_runtime.MOBILITY_HOUSE_BACKEND_PARAMETER_KEY else "enabled"

    monkeypatch.setattr(simulator_runtime, "get_feature_parameter", _param)
    monkeypatch.setattr(simulator_runtime, "find_spec", lambda _: None)

    selection = simulator_runtime.resolve_simulator_backend(preferred_backend="mobilityhouse")

    assert selection.use_mobility_house is False
    assert selection.backend == "legacy"
    assert "legacy" in selection.reason.lower()


def test_backend_selection_reports_disabled_when_all_backends_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selection should report disabled state when both backend parameters are disabled."""

    monkeypatch.setattr(simulator_runtime, "get_feature_parameter", lambda *args, **kwargs: "disabled")
    monkeypatch.setattr(simulator_runtime, "find_spec", lambda _: object())

    selection = simulator_runtime.resolve_simulator_backend()

    assert selection.use_mobility_house is False
    assert selection.backend == "legacy"
    assert "disabled" in selection.reason.lower()
