from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from apps.core.system import lifecycle
from apps.core.system.lifecycle_ownership import LifecycleMode, classify_managed_installation


class ManagedUpdateError(RuntimeError):
    """Raised when a managed production update cannot complete safely."""


def _require_managed(current: lifecycle.InstallationLayout) -> None:
    ownership = classify_managed_installation(
        current.root,
        checkout_name=current.checkout.name,
    )
    if ownership.mode is not LifecycleMode.MANAGED or not ownership.valid:
        detail = "; ".join(ownership.problems) or "managed ownership metadata is missing"
        raise ManagedUpdateError(f"managed ownership check failed: {detail}")


def _run_phase(name: str, operation) -> None:
    try:
        operation()
    except ManagedUpdateError:
        raise
    except Exception as exc:
        raise ManagedUpdateError(f"{name} phase failed: {exc}") from exc


def _service_profile(current: lifecycle.InstallationLayout) -> str:
    return lifecycle._current_role(current)


def _reconcile_services(current: lifecycle.InstallationLayout) -> None:
    gway = shutil.which("gway")
    if gway is None:
        raise ManagedUpdateError("service reconciliation requires the GWAY executable")

    profile = _service_profile(current)
    env = os.environ.copy()
    env["GWAY_SERVICE_PROFILE"] = profile
    subprocess.run(
        [gway, "service", "install", "arthexis"],
        check=True,
        text=True,
        env=env,
    )


def _verify_health(current: lifecycle.InstallationLayout) -> None:
    lifecycle.run_manage("status", "--json", layout=current)


def upgrade(
    *arguments: str,
    layout: lifecycle.InstallationLayout | None = None,
) -> lifecycle.InstallationLayout:
    """Complete the Arthexis-owned part of a GWAY managed update transaction.

    GWAY owns repository/environment refresh and rolls those resources back when
    this hook fails. Arthexis owns application preparation, service-profile
    reconciliation, lifecycle health verification, and ownership confirmation.
    """

    current = lifecycle._resolve_layout(layout)
    _require_managed(current)

    prepared: lifecycle.InstallationLayout | None = None

    def prepare_application() -> None:
        nonlocal prepared
        prepared = lifecycle._prepare_for_options(arguments, layout=current)

    _run_phase("application preparation", prepare_application)
    assert prepared is not None
    _run_phase("service reconciliation", lambda: _reconcile_services(prepared))
    _run_phase("health verification", lambda: _verify_health(prepared))
    _run_phase("ownership confirmation", lambda: lifecycle._record_managed_ownership(prepared))
    return prepared


def persistent_paths(root: str | Path | None = None) -> tuple[Path, ...]:
    """Expose persistent managed paths for update-preservation tests and tooling."""
    current = lifecycle.layout(root)
    return (current.root / "var" / "lib",)
