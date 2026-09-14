from __future__ import annotations

import shutil
from pathlib import Path

from apps.core.system.lifecycle import InstallationLayout, _resolve_layout
from apps.core.system.lifecycle_ownership import (
    LifecycleMode,
    OwnershipLayout,
    classify_managed_installation,
)


class ManagedUninstallError(RuntimeError):
    """Raised when Arthexis cannot safely clean a managed installation."""


def _require_managed(current: InstallationLayout) -> OwnershipLayout:
    ownership = classify_managed_installation(
        current.root,
        checkout_name=current.checkout.name,
    )
    if ownership.mode is not LifecycleMode.MANAGED or not ownership.valid:
        detail = "; ".join(ownership.problems) or "managed ownership metadata is missing"
        raise ManagedUninstallError(f"managed ownership check failed: {detail}")
    return ownership.layout


def _remove_disposable_state(layout: OwnershipLayout) -> None:
    """Remove Arthexis-owned disposable state while preserving instance data."""
    for directory in (layout.logs, layout.cache, layout.run):
        shutil.rmtree(directory, ignore_errors=True)

    layout.metadata.unlink(missing_ok=True)
    metadata_dir = layout.metadata.parent
    try:
        metadata_dir.rmdir()
    except OSError:
        pass


def uninstall(
    *arguments: str,
    layout: InstallationLayout | None = None,
) -> InstallationLayout:
    """Prepare a managed Arthexis installation for GWAY removal.

    GWAY has already stopped and removed manifest-defined services before this
    hook executes. The hook validates ownership, removes only application-owned
    disposable runtime state, and deliberately leaves ``var/lib`` untouched.
    GWAY removes the managed checkout/environment after this hook succeeds.
    """
    if arguments:
        raise ManagedUninstallError(
            "managed uninstall does not accept destructive data-removal arguments"
        )

    current = _resolve_layout(layout)
    ownership_layout = _require_managed(current)
    _remove_disposable_state(ownership_layout)
    return current


def persistent_paths(root: str | Path | None = None) -> tuple[Path, ...]:
    if root is None:
        current = _resolve_layout(None)
    else:
        selected_root = Path(root).expanduser()
        current = InstallationLayout(
            root=selected_root,
            checkout=selected_root / "app",
        )
    return (current.root / "var" / "lib",)
