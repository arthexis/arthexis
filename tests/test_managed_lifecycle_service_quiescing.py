from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_INTEGRATION = ROOT / "scripts" / "ci" / "live-integration.sh"


def test_live_integration_repeats_service_managed_install() -> None:
    """Watchtower must exercise reinstall through GWay's service-aware boundary."""
    script = LIVE_INTEGRATION.read_text(encoding="utf-8")

    managed_install = (
        'gway install arthexis --service --role "$GWAY_SERVICE_PROFILE"'
    )

    # The second invocation is the regression gate for the SQLite lock that
    # occurs when a reinstall mutates application state while managed services
    # are still running. Service quiescing belongs to GWay, so Arthexis tests
    # the externally visible contract instead of expecting lifecycle.py to call
    # systemctl directly.
    assert script.count(managed_install) >= 2
    assert 'phase="managed-install-first"' in script
    assert 'phase="managed-install-second"' in script


def test_arthexis_lifecycle_does_not_own_system_service_control() -> None:
    """Application lifecycle hooks remain independent of systemd orchestration."""
    lifecycle = (ROOT / "apps" / "core" / "system" / "lifecycle.py").read_text(
        encoding="utf-8"
    )

    assert "systemctl" not in lifecycle
    assert "gway-arthexis-" not in lifecycle
