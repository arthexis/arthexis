import subprocess
import tomllib
from pathlib import Path

from apps.core.system import lifecycle


ROOT = Path(__file__).resolve().parents[1]


def test_managed_run_python_uses_runtime_state_outside_checkout(
    monkeypatch, tmp_path
) -> None:
    checkout = tmp_path / "app"
    checkout.mkdir()
    current = lifecycle.InstallationLayout(root=tmp_path, checkout=checkout)
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(lifecycle, "current_python", lambda: "/python")
    monkeypatch.setattr(lifecycle.subprocess, "run", fake_run)

    lifecycle.run_python(["manage.py", "check"], layout=current)

    env = captured["env"]
    assert env["ARTHEXIS_MODE"] == "installed"
    assert env["ARTHEXIS_DATA_DIR"] == str(tmp_path / "var" / "lib")
    assert env["ARTHEXIS_LOG_DIR"] == str(tmp_path / "var" / "log")
    assert env["ARTHEXIS_CACHE_DIR"] == str(tmp_path / "var" / "cache")
    assert env["ARTHEXIS_RUN_DIR"] == str(tmp_path / "var" / "run")
    assert captured["cwd"] == checkout


def test_prepare_runtime_state_migrates_legacy_sqlite_once(tmp_path) -> None:
    checkout = tmp_path / "app"
    checkout.mkdir()
    legacy_database = checkout / "db.sqlite3"
    legacy_database.write_bytes(b"legacy database")
    current = lifecycle.InstallationLayout(root=tmp_path, checkout=checkout)

    state_root = lifecycle._prepare_runtime_state(current)
    managed_database = tmp_path / "var" / "lib" / "db.sqlite3"

    assert state_root == tmp_path / "var"
    assert managed_database.read_bytes() == b"legacy database"

    managed_database.write_bytes(b"managed database")
    legacy_database.write_bytes(b"changed legacy database")
    lifecycle._prepare_runtime_state(current)

    assert managed_database.read_bytes() == b"managed database"


def test_managed_services_use_installed_runtime_state() -> None:
    manifest = tomllib.loads((ROOT / "gway.toml").read_text(encoding="utf-8"))
    expected_environment = {
        "PYTHONUNBUFFERED": "1",
        "ARTHEXIS_MODE": "installed",
        "ARTHEXIS_DATA_DIR": "/opt/arthexis/var/lib",
        "ARTHEXIS_LOG_DIR": "/opt/arthexis/var/log",
        "ARTHEXIS_CACHE_DIR": "/opt/arthexis/var/cache",
        "ARTHEXIS_RUN_DIR": "/opt/arthexis/var/run",
    }

    for service_name in ("web-local", "web-edge", "worker", "beat"):
        assert manifest["services"][service_name]["environment"] == expected_environment
