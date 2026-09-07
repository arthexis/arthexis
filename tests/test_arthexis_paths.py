from pathlib import Path

import pytest

from utils.arthexis_paths import ArthexisMode, resolve_arthexis_paths


def test_checkout_mode_is_default_and_checkout_local(tmp_path: Path) -> None:
    paths = resolve_arthexis_paths(project_root=tmp_path, environ={})

    assert paths.mode is ArthexisMode.CHECKOUT
    assert paths.app_dir == tmp_path
    assert paths.config_dir == tmp_path / ".arthexis" / "config"
    assert paths.data_dir == tmp_path / ".arthexis" / "data"
    assert paths.log_dir == tmp_path / ".arthexis" / "log"
    assert paths.cache_dir == tmp_path / ".arthexis" / "cache"
    assert paths.run_dir == tmp_path / ".arthexis" / "run"


def test_installed_mode_uses_standard_linux_defaults(tmp_path: Path) -> None:
    paths = resolve_arthexis_paths(
        project_root=tmp_path,
        mode="installed",
        environ={},
    )

    assert paths.mode is ArthexisMode.INSTALLED
    assert paths.app_dir == Path("/opt/arthexis/current")
    assert paths.config_dir == Path("/etc/arthexis")
    assert paths.data_dir == Path("/var/lib/arthexis")
    assert paths.log_dir == Path("/var/log/arthexis")
    assert paths.cache_dir == Path("/var/cache/arthexis")
    assert paths.run_dir == Path("/run/arthexis")


def test_mode_can_be_selected_from_environment(tmp_path: Path) -> None:
    paths = resolve_arthexis_paths(
        project_root=tmp_path,
        environ={"ARTHEXIS_MODE": "installed"},
    )

    assert paths.mode is ArthexisMode.INSTALLED


def test_explicit_mode_wins_over_environment(tmp_path: Path) -> None:
    paths = resolve_arthexis_paths(
        project_root=tmp_path,
        mode=ArthexisMode.CHECKOUT,
        environ={"ARTHEXIS_MODE": "installed"},
    )

    assert paths.mode is ArthexisMode.CHECKOUT
    assert paths.app_dir == tmp_path


def test_all_paths_can_be_redirected_without_root(tmp_path: Path) -> None:
    overrides = {
        "ARTHEXIS_APP_DIR": str(tmp_path / "app"),
        "ARTHEXIS_CONFIG_DIR": str(tmp_path / "config"),
        "ARTHEXIS_DATA_DIR": str(tmp_path / "data"),
        "ARTHEXIS_LOG_DIR": str(tmp_path / "log"),
        "ARTHEXIS_CACHE_DIR": str(tmp_path / "cache"),
        "ARTHEXIS_RUN_DIR": str(tmp_path / "run"),
    }

    paths = resolve_arthexis_paths(
        project_root=tmp_path / "source",
        mode="installed",
        environ=overrides,
    )

    assert paths.app_dir == tmp_path / "app"
    assert paths.writable_dirs == (
        tmp_path / "config",
        tmp_path / "data",
        tmp_path / "log",
        tmp_path / "cache",
        tmp_path / "run",
    )


def test_resolution_has_no_filesystem_side_effects(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"

    paths = resolve_arthexis_paths(project_root=checkout, environ={})

    assert paths.app_dir == checkout
    assert not checkout.exists()
    assert all(not path.exists() for path in paths.writable_dirs)


def test_invalid_mode_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown Arthexis mode"):
        resolve_arthexis_paths(
            project_root=tmp_path,
            mode="automatic",
            environ={},
        )
