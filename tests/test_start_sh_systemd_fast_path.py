from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _setup_start_runtime(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = Path(__file__).resolve().parents[1]
    runtime_root = tmp_path / "runtime"
    helpers_root = runtime_root / "scripts" / "helpers"
    helpers_root.mkdir(parents=True, exist_ok=True)

    shutil.copy2(repo_root / "start.sh", runtime_root / "start.sh")
    shutil.copytree(repo_root / "scripts" / "helpers", helpers_root, dirs_exist_ok=True)

    service_start = runtime_root / "scripts" / "service-start.sh"
    _write_executable(
        service_start,
        """#!/usr/bin/env bash
set -e
echo "$*" > "${ARTHEXIS_SERVICE_START_ARGS_FILE:-/tmp/arthexis-service-start-args}"
echo "called" > "${ARTHEXIS_SERVICE_START_CALLED_FILE:-/tmp/arthexis-service-start-called}"
""",
    )

    locks_dir = runtime_root / ".locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    (locks_dir / "service.lck").write_text("demo\n", encoding="utf-8")
    (locks_dir / "rfid-service.lck").write_text("configured\n", encoding="utf-8")
    (locks_dir / "camera-service.lck").write_text("configured\n", encoding="utf-8")

    fake_bin = runtime_root / "fakebin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    _write_executable(
        fake_bin / "sudo",
        """#!/usr/bin/env bash
if [ "${1:-}" = "-n" ]; then
  shift
fi
exec "$@"
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        """#!/usr/bin/env bash
set -e

log_file="${SYSTEMCTL_LOG_FILE:?}"
cmd="${1:-}"
shift || true

unit_var_name() {
  local unit="$1"
  printf '%s' "${unit^^}" | tr -c 'A-Z0-9' '_'
}

unit_status() {
  local key
  key="$(unit_var_name "$1")"
  local value="${!key:-inactive}"
  echo "$value"
  if [ "$value" = "active" ]; then
    return 0
  fi
  return 1
}

printf '%s %s\\n' "$cmd" "$*" >> "$log_file"

case "$cmd" in
  list-unit-files)
    unit="${3:-}"
    if [ -z "$unit" ]; then
      unit="${1:-}"
    fi
    echo "${unit} enabled"
    ;;
  is-active)
    unit="${1:-}"
    unit_status "$unit"
    ;;
  restart|start)
    ;;
  show)
    unit="${1:-}"
    shift || true
    while [ $# -gt 0 ]; do
      if [ "$1" = "--property=ActiveState" ]; then
        echo "active"
        exit 0
      fi
      if [ "$1" = "--property=SubState" ]; then
        echo "running"
        exit 0
      fi
      if [ "$1" = "--property=Result" ]; then
        echo "success"
        exit 0
      fi
      shift
    done
    echo "active"
    ;;
  status)
    exit 0
    ;;
  *)
    echo "unexpected systemctl invocation: $cmd $*" >&2
    exit 1
    ;;
esac
""",
    )

    return runtime_root, fake_bin


def test_start_sh_skips_restart_when_main_service_is_active(tmp_path: Path) -> None:
    runtime_root, fake_bin = _setup_start_runtime(tmp_path)
    systemctl_log = tmp_path / "systemctl.log"
    service_start_called = tmp_path / "service-start.called"

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SYSTEMCTL_LOG_FILE"] = str(systemctl_log)
    env["DEMO"] = "active"
    env["ARTHEXIS_SERVICE_START_CALLED_FILE"] = str(service_start_called)

    result = subprocess.run(
        ["bash", "start.sh"],
        cwd=runtime_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert not service_start_called.exists()
    calls = systemctl_log.read_text(encoding="utf-8")
    assert "restart demo" not in calls
    assert "is-active demo" in calls
    assert not (runtime_root / ".locks" / "suite_uptime.lck").exists()


def test_start_sh_restarts_when_main_service_is_inactive(tmp_path: Path) -> None:
    runtime_root, fake_bin = _setup_start_runtime(tmp_path)
    systemctl_log = tmp_path / "systemctl.log"

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SYSTEMCTL_LOG_FILE"] = str(systemctl_log)
    env["DEMO"] = "inactive"

    result = subprocess.run(
        ["bash", "start.sh"],
        cwd=runtime_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    calls = systemctl_log.read_text(encoding="utf-8")
    assert "is-active demo" in calls
    assert "restart demo" in calls
    assert "show demo --property=ActiveState --value" in calls


def test_start_sh_reload_path_preserves_service_start_flow(tmp_path: Path) -> None:
    runtime_root, fake_bin = _setup_start_runtime(tmp_path)
    systemctl_log = tmp_path / "systemctl.log"
    service_start_called = tmp_path / "service-start.called"
    service_start_args = tmp_path / "service-start.args"

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SYSTEMCTL_LOG_FILE"] = str(systemctl_log)
    env["DEMO"] = "active"
    env["ARTHEXIS_SERVICE_START_CALLED_FILE"] = str(service_start_called)
    env["ARTHEXIS_SERVICE_START_ARGS_FILE"] = str(service_start_args)

    result = subprocess.run(
        ["bash", "start.sh", "--reload"],
        cwd=runtime_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert service_start_called.exists()
    assert service_start_args.read_text(encoding="utf-8").strip() == "--reload"
    calls = systemctl_log.read_text(encoding="utf-8")
    assert "restart demo" not in calls


def test_start_sh_restarts_failed_companion_when_main_is_active(tmp_path: Path) -> None:
    runtime_root, fake_bin = _setup_start_runtime(tmp_path)
    systemctl_log = tmp_path / "systemctl.log"

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SYSTEMCTL_LOG_FILE"] = str(systemctl_log)
    env["DEMO"] = "active"
    env["RFID_DEMO"] = "failed"
    env["CAMERA_DEMO"] = "active"

    result = subprocess.run(
        ["bash", "start.sh"],
        cwd=runtime_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    calls = systemctl_log.read_text(encoding="utf-8")
    assert "is-active demo" in calls
    assert "is-active rfid-demo" in calls
    assert "restart rfid-demo" in calls
    assert "\nstart rfid-demo\n" not in f"\n{calls}\n"
    assert "restart demo" not in calls


def test_start_sh_starts_inactive_companion_when_main_is_active(tmp_path: Path) -> None:
    runtime_root, fake_bin = _setup_start_runtime(tmp_path)
    systemctl_log = tmp_path / "systemctl.log"

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SYSTEMCTL_LOG_FILE"] = str(systemctl_log)
    env["DEMO"] = "active"
    env["RFID_DEMO"] = "inactive"
    env["CAMERA_DEMO"] = "active"

    result = subprocess.run(
        ["bash", "start.sh"],
        cwd=runtime_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    calls = systemctl_log.read_text(encoding="utf-8")
    assert "is-active rfid-demo" in calls
    assert "start rfid-demo" in calls
    assert "\nrestart rfid-demo\n" not in f"\n{calls}\n"
    assert "restart demo" not in calls
