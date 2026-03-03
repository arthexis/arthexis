"""Tests for liboqs service helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.liboqs.services import OqsImportError, discover_algorithms


@pytest.mark.critical
def test_discover_algorithms_raises_specific_error_without_oqs(monkeypatch: pytest.MonkeyPatch):
    """Regression: missing bindings should raise a dedicated, actionable error."""

    def _raise_not_found(_name: str):
        raise ModuleNotFoundError("No module named 'oqs'")

    monkeypatch.setattr("apps.liboqs.services.import_module", _raise_not_found)

    with pytest.raises(OqsImportError):
        discover_algorithms()


@pytest.mark.critical
def test_discover_algorithms_returns_sorted_mechanisms(monkeypatch: pytest.MonkeyPatch):
    """Regression: discovered algorithm names should be stable and sorted."""

    fake_oqs = SimpleNamespace(
        get_enabled_kem_mechanisms=lambda: ["Kyber512", "Kyber1024"],
        get_enabled_sig_mechanisms=lambda: ["Dilithium5", "Dilithium2"],
    )
    monkeypatch.setattr("apps.liboqs.services.import_module", lambda _name: fake_oqs)

    assert discover_algorithms() == {
        "kem": ["Kyber1024", "Kyber512"],
        "signature": ["Dilithium2", "Dilithium5"],
    }
