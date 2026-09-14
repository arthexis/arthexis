from __future__ import annotations

from pathlib import Path

import pytest

from apps.core.system import lifecycle, managed_uninstall
from apps.core.system.lifecycle_ownership import record_managed_installation


def _managed_layout(tmp_path: Path) -> lifecycle.InstallationLayout:
    current = lifecycle.InstallationLayout(root=tmp_path, checkout=tmp_path / "app")
    current.checkout.mkdir()
    (tmp_path / ".venv").mkdir()
    for directory in ("lib", "log", "cache", "run"):
        path = tmp_path / "var" / directory
        path.mkdir(parents=True)
        (path / "sentinel").write_text(directory, encoding="utf-8")
    record_managed_installation(tmp_path, checkout_name="app")
    return current


def test_uninstall_preserves_persistent_data_and_checkout_for_gway(tmp_path: Path) -> None:
    current = _managed_layout(tmp_path)
    data_sentinel = tmp_path / "var" / "lib" / "sentinel"

    result = managed_uninstall.uninstall(layout=current)

    assert result is current
    assert current.checkout.exists()
    assert (tmp_path / ".venv").exists()
    assert data_sentinel.read_text(encoding="utf-8") == "lib"
    assert not (tmp_path / "var" / "log").exists()
    assert not (tmp_path / "var" / "cache").exists()
    assert not (tmp_path / "var" / "run").exists()
    assert not (tmp_path / ".gway" / "arthexis.json").exists()


def test_uninstall_rejects_unmanaged_layout(tmp_path: Path) -> None:
    current = lifecycle.InstallationLayout(root=tmp_path, checkout=tmp_path / "app")
    current.checkout.mkdir()

    with pytest.raises(managed_uninstall.ManagedUninstallError, match="ownership"):
        managed_uninstall.uninstall(layout=current)


def test_uninstall_rejects_destructive_arguments(tmp_path: Path) -> None:
    current = _managed_layout(tmp_path)

    with pytest.raises(
        managed_uninstall.ManagedUninstallError,
        match="does not accept destructive",
    ):
        managed_uninstall.uninstall("--purge-data", layout=current)

    assert (tmp_path / "var" / "lib" / "sentinel").exists()
    assert (tmp_path / ".gway" / "arthexis.json").exists()


def test_persistent_paths_names_only_managed_data_root(tmp_path: Path) -> None:
    assert managed_uninstall.persistent_paths(tmp_path) == (tmp_path / "var" / "lib",)
