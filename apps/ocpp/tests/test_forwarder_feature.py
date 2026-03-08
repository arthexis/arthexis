"""Regression tests for the OCPP Forwarder suite feature toggle."""

from types import SimpleNamespace

import pytest

from apps.features.models import Feature
from apps.ocpp.forwarder import Forwarder
from apps.ocpp.forwarder_feature import OCPP_FORWARDER_FEATURE_SLUG, ocpp_forwarder_enabled
from apps.ocpp.tasks.forwarding import setup_forwarders


pytestmark = pytest.mark.django_db


def test_ocpp_forwarder_enabled_reads_suite_feature_flag() -> None:
    """Regression: helper should mirror the OCPP Forwarder suite feature state."""

    Feature.objects.filter(slug=OCPP_FORWARDER_FEATURE_SLUG).update(is_enabled=False)

    assert ocpp_forwarder_enabled(default=True) is False


def test_setup_forwarders_skips_sync_when_suite_feature_disabled(monkeypatch) -> None:
    """Regression: disabled suite feature must short-circuit forwarding sync tasks."""

    Feature.objects.filter(slug=OCPP_FORWARDER_FEATURE_SLUG).update(is_enabled=False)
    clear_calls: list[str] = []

    monkeypatch.setattr(
        "apps.ocpp.tasks.forwarding.forwarder.sync_forwarded_charge_points",
        lambda: pytest.fail("sync should not run when OCPP Forwarder is disabled"),
    )
    monkeypatch.setattr(
        "apps.ocpp.tasks.forwarding.forwarder.clear_sessions",
        lambda: clear_calls.append("cleared"),
    )

    assert setup_forwarders() == 0
    assert clear_calls == ["cleared"]


def test_sync_forwarded_charge_points_clears_sessions_when_feature_disabled(monkeypatch) -> None:
    """Forwarder service should clear active sessions and skip sync when feature is off."""

    Feature.objects.filter(slug=OCPP_FORWARDER_FEATURE_SLUG).update(is_enabled=False)
    forwarder = Forwarder()
    fake_session = SimpleNamespace(connection=SimpleNamespace(close=lambda: None))
    forwarder._sessions[99] = fake_session

    update_calls: list[set[int]] = []

    class _DummyManager:
        def update_running_state(self, active_ids):
            update_calls.append(set(active_ids))

    class _DummyCPForwarder:
        objects = _DummyManager()

    monkeypatch.setattr("apps.ocpp.models.CPForwarder", _DummyCPForwarder)

    connected = forwarder.sync_forwarded_charge_points(refresh_forwarders=False)

    assert connected == 0
    assert forwarder._sessions == {}
    assert update_calls == [set()]
