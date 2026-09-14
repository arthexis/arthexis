from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.core.system import lifecycle, managed_update
from apps.core.system.lifecycle_ownership import record_managed_installation


def _managed_layout(tmp_path):
    current = lifecycle.InstallationLayout(root=tmp_path, checkout=tmp_path / "app")
    current.checkout.mkdir()
    record_managed_installation(tmp_path, checkout_name="app")
    return current


def test_managed_update_runs_prepare_services_health_then_ownership(
    monkeypatch, tmp_path
):
    current = _managed_layout(tmp_path)
    calls = []

    monkeypatch.setattr(
        lifecycle,
        "_prepare_for_options",
        lambda arguments, *, layout: calls.append(("prepare", arguments, layout))
        or layout,
    )
    monkeypatch.setattr(
        managed_update,
        "_reconcile_services",
        lambda layout: calls.append(("services", layout)),
    )
    monkeypatch.setattr(
        managed_update,
        "_verify_health",
        lambda layout: calls.append(("health", layout)),
    )
    monkeypatch.setattr(
        lifecycle,
        "_record_managed_ownership",
        lambda layout: calls.append(("ownership", layout)),
    )

    result = managed_update.upgrade("--role", "Terminal", layout=current)

    assert result is current
    assert calls == [
        ("prepare", ("--role", "Terminal"), current),
        ("services", current),
        ("health", current),
        ("ownership", current),
    ]


def test_managed_update_rejects_unmanaged_checkout(tmp_path):
    current = lifecycle.InstallationLayout(root=tmp_path, checkout=tmp_path / "app")
    current.checkout.mkdir()

    with pytest.raises(managed_update.ManagedUpdateError, match="ownership"):
        managed_update.upgrade(layout=current)


def test_failed_health_does_not_confirm_ownership(monkeypatch, tmp_path):
    current = _managed_layout(tmp_path)
    calls = []

    monkeypatch.setattr(
        lifecycle,
        "_prepare_for_options",
        lambda arguments, *, layout: layout,
    )
    monkeypatch.setattr(managed_update, "_reconcile_services", lambda layout: None)

    def fail_health(layout):
        raise RuntimeError("not healthy")

    monkeypatch.setattr(managed_update, "_verify_health", fail_health)
    monkeypatch.setattr(
        lifecycle,
        "_record_managed_ownership",
        lambda layout: calls.append("ownership"),
    )

    with pytest.raises(
        managed_update.ManagedUpdateError,
        match="health verification phase",
    ):
        managed_update.upgrade(layout=current)

    assert calls == []


def test_service_reconciliation_uses_persisted_role(monkeypatch, tmp_path):
    current = _managed_layout(tmp_path)
    lock = current.checkout / ".locks" / "role.lck"
    lock.parent.mkdir(parents=True)
    lock.write_text("Control\n", encoding="utf-8")
    calls = []

    monkeypatch.setattr(managed_update.shutil, "which", lambda name: "/usr/bin/gway")

    def fake_run(arguments, *, check, text, env):
        calls.append((arguments, check, text, env["GWAY_SERVICE_PROFILE"]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(managed_update.subprocess, "run", fake_run)

    managed_update._reconcile_services(current)

    assert calls == [
        (
            ["/usr/bin/gway", "service", "install", "arthexis"],
            True,
            True,
            "Control",
        )
    ]
