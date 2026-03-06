"""Regression tests for simulator backend selection overrides."""

import pytest

from apps.simulators import simulator_runtime


@pytest.mark.parametrize("alias", ["arthexis", "legacy"])
def test_backend_override_forces_arthexis(alias: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Selecting the Arthexis backend should force the legacy runtime path."""

    monkeypatch.setattr(simulator_runtime, "is_suite_feature_enabled", lambda *args, **kwargs: True)
    monkeypatch.setattr(simulator_runtime, "find_spec", lambda _: object())

    selection = simulator_runtime.resolve_simulator_backend(preferred_backend=alias)

    assert selection.use_mobility_house is False
    assert selection.backend == "legacy"


def test_backend_override_uses_mobilityhouse_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting Mobility House should use v2 when feature and dependency are present."""

    monkeypatch.setattr(simulator_runtime, "is_suite_feature_enabled", lambda *args, **kwargs: True)
    monkeypatch.setattr(simulator_runtime, "find_spec", lambda _: object())

    selection = simulator_runtime.resolve_simulator_backend(preferred_backend="mobilityhouse")

    assert selection.use_mobility_house is True
    assert selection.backend == "mobility_house"


def test_backend_override_falls_back_when_mobilityhouse_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting Mobility House should fall back when optional dependency is absent."""

    monkeypatch.setattr(simulator_runtime, "is_suite_feature_enabled", lambda *args, **kwargs: True)
    monkeypatch.setattr(simulator_runtime, "find_spec", lambda _: None)

    selection = simulator_runtime.resolve_simulator_backend(preferred_backend="mobilityhouse")

    assert selection.use_mobility_house is False
    assert selection.backend == "legacy"
    assert "falling back" in selection.reason.lower()
