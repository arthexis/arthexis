from __future__ import annotations

from pathlib import Path

from apps.core.system import lifecycle_status
from apps.core.system.lifecycle_ownership import record_managed_installation


def _stub_runtime(monkeypatch, *, role="Terminal", pending=False, health="GOOD"):
    monkeypatch.setattr(lifecycle_status, "node_role", lambda: role)
    monkeypatch.setattr(lifecycle_status, "version", lambda: "1.2.3")
    monkeypatch.setattr(lifecycle_status, "application_status", lambda: health)
    monkeypatch.setattr(lifecycle_status, "_migration_state", lambda: (pending, None))

    def git_value(_checkout: Path, *arguments: str):
        if arguments == ("rev-parse", "HEAD"):
            return "abc123"
        if arguments == ("status", "--porcelain"):
            return ""
        raise AssertionError(arguments)

    monkeypatch.setattr(lifecycle_status, "_git_value", git_value)


def test_unmanaged_checkout_reports_lifecycle_without_claiming_services(monkeypatch, tmp_path):
    checkout = tmp_path / "source"
    checkout.mkdir()
    _stub_runtime(monkeypatch)
    monkeypatch.setattr(
        lifecycle_status,
        "_service_state",
        lambda unit: (_ for _ in ()).throw(AssertionError(unit)),
    )

    report = lifecycle_status.inspect_lifecycle(checkout)

    assert report["mode"] == "unmanaged"
    assert report["state"] == "unmanaged"
    assert report["checkout"] == str(checkout)
    assert report["services"] == []
    assert report["application_health"] == "GOOD"


def test_managed_status_reports_identity_layout_revision_and_services(monkeypatch, tmp_path):
    checkout = tmp_path / "app"
    checkout.mkdir()
    (tmp_path / ".venv").mkdir()
    recorded = record_managed_installation(tmp_path)
    _stub_runtime(monkeypatch)
    monkeypatch.setattr(
        lifecycle_status,
        "_service_state",
        lambda unit: {"unit": unit, "active": "active", "enabled": "enabled"},
    )

    report = lifecycle_status.inspect_lifecycle(checkout)

    assert report["mode"] == "managed"
    assert report["state"] == "healthy"
    assert report["installation_id"] == recorded.installation_id
    assert report["root"] == str(tmp_path)
    assert report["environment"] == str(tmp_path / ".venv")
    assert report["persistent_data"] == str(tmp_path / "var" / "lib")
    assert report["version"] == "1.2.3"
    assert report["revision"] == "abc123"
    assert report["dirty"] is False
    assert report["role"] == "Terminal"
    assert report["pending_migrations"] is False
    assert report["services"] == [
        {
            "unit": "gway-arthexis-web-local.service",
            "active": "active",
            "enabled": "enabled",
        }
    ]
    assert report["problems"] == []


def test_managed_status_exposes_actionable_degraded_state(monkeypatch, tmp_path):
    checkout = tmp_path / "app"
    checkout.mkdir()
    record_managed_installation(tmp_path)
    _stub_runtime(monkeypatch, role="Control", pending=True, health="FAIL")
    monkeypatch.setattr(
        lifecycle_status,
        "_service_state",
        lambda unit: {"unit": unit, "active": "inactive", "enabled": "enabled"},
    )

    report = lifecycle_status.inspect_lifecycle(checkout)

    assert report["state"] == "degraded"
    assert "managed Python environment is missing" in report["problems"]
    assert "database has pending migrations" in report["problems"]
    assert "application health is not GOOD" in report["problems"]
    assert any(problem.startswith("service is not active:") for problem in report["problems"])
    assert {service["unit"] for service in report["services"]} == {
        "gway-arthexis-web-edge.service",
        "gway-arthexis-worker.service",
        "gway-arthexis-beat.service",
    }


def test_rendered_status_keeps_operator_facing_lifecycle_summary(monkeypatch, tmp_path):
    checkout = tmp_path / "source"
    checkout.mkdir()
    _stub_runtime(monkeypatch)

    rendered = lifecycle_status.render_lifecycle_status(
        lifecycle_status.inspect_lifecycle(checkout)
    )

    assert "Lifecycle: unmanaged (unmanaged)" in rendered
    assert "Version: 1.2.3" in rendered
    assert f"Checkout: {checkout}" in rendered
    assert "Application health: GOOD" in rendered
