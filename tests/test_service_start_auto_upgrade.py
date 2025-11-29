import os
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX-compatible shell")


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)


def _write_python_stub(path: Path, content: str) -> None:
    _make_executable(path, content)


DEFAULT_PYTHON_STUB = "\n".join(
    [
        "#!/usr/bin/env bash",
        "if [ -n \"${COMMAND_LOG:-}\" ]; then",
        "  echo \"python $@\" >> \"$COMMAND_LOG\"",
        "fi",
        "exit 0",
    ]
)


RUNSERVER_SOCKET_STUB = textwrap.dedent(
    """#!/usr/bin/env bash
    if [[ "$1" == "manage.py" && "$2" == "runserver" ]]; then
      if [ -n "${FAIL_RUNSERVER:-}" ]; then
        echo "runserver failed" >&2
        exit 1
      fi

      port_arg="${3:-}"
      port="${port_arg##*:}"

      python3 - "$port" "${READY_FILE:-}" <<'PY'
import socket
import sys
from pathlib import Path

port_arg = sys.argv[1] or "0"
ready_file = sys.argv[2]
port = int(port_arg)

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.listen()
    if ready_file:
        Path(ready_file).write_text("ready\n", encoding="utf-8")
    while True:
        conn, _ = sock.accept()
        with conn:
            conn.sendall(b"OK")
PY

      exit 0
    fi

    if [ -n "${COMMAND_LOG:-}" ]; then
      echo "python $@" >> "${COMMAND_LOG}"
    fi
    exit 0
    """
)


def _prepare_service_start_tree(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "service-start"
    base.mkdir()

    shutil.copy2(REPO_ROOT / "service-start.sh", base / "service-start.sh")
    shutil.copytree(REPO_ROOT / "scripts", base / "scripts")

    (base / "logs").mkdir()
    locks = base / "locks"
    locks.mkdir()

    upgrade_log = base / "upgrade.log"
    _make_executable(
        base / "upgrade.sh",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f"echo \"$@\" >> '{upgrade_log}'",
                "exit 0",
            ]
        ),
    )

    venv_bin = base / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    _make_executable(
        venv_bin / "activate",
        "\n".join([
            "#!/usr/bin/env bash",
            "VENV_DIR=\"$(cd \"$(dirname \"$BASH_SOURCE\")\" && pwd)\"",
            "export PATH=\"$VENV_DIR:$PATH\"",
        ]),
    )
    _write_python_stub(venv_bin / "python", DEFAULT_PYTHON_STUB)
    _make_executable(
        venv_bin / "celery",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "if [ -n \"${COMMAND_LOG:-}\" ]; then",
                "  echo \"celery $@\" >> \"$COMMAND_LOG\"",
                "fi",
                "exit 0",
            ]
        ),
    )

    return base, upgrade_log


def _run_service_start(
    base: Path,
    *,
    extra_env: dict[str, str] | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("TZ", "UTC")
    env["PATH"] = f"{base / '.venv' / 'bin'}:{env['PATH']}"
    if extra_env:
        env.update(extra_env)

    command = [
        "bash",
        str(base / "service-start.sh"),
        "--no-celery",
        "--port",
        "9999",
    ]
    if extra_args:
        command.extend(extra_args)

    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=base,
        env=env,
    )


def test_service_start_ignores_auto_upgrade_lock(tmp_path: Path) -> None:
    base, upgrade_log = _prepare_service_start_tree(tmp_path)
    mode_file = base / "locks" / "auto_upgrade.lck"
    mode_file.write_text("LATEST\n", encoding="utf-8")

    result = _run_service_start(base)

    assert result.returncode == 0
    assert not upgrade_log.exists() or upgrade_log.read_text(encoding="utf-8").strip() == ""
    assert mode_file.exists()


def test_service_start_does_not_run_upgrade_when_skip_lock_present(tmp_path: Path) -> None:
    base, upgrade_log = _prepare_service_start_tree(tmp_path)
    mode_file = base / "locks" / "auto_upgrade.lck"
    mode_file.write_text("stable\n", encoding="utf-8")

    skip_lock = base / "locks" / "service-start-skip.lck"
    skip_lock.write_text("skip once\n", encoding="utf-8")
    now = time.time()
    os.utime(skip_lock, (now, now))

    result = _run_service_start(base)

    assert result.returncode == 0
    assert not upgrade_log.exists() or upgrade_log.read_text(encoding="utf-8").strip() == ""
    assert skip_lock.exists()


def test_service_start_waits_for_reachability(tmp_path: Path) -> None:
    base, _ = _prepare_service_start_tree(tmp_path)
    ready_file = base / "ready.txt"
    _write_python_stub(base / ".venv" / "bin" / "python", RUNSERVER_SOCKET_STUB)

    result = _run_service_start(
        base,
        extra_env={"READY_FILE": str(ready_file)},
        extra_args=["--await"],
    )

    assert result.returncode == 0
    assert ready_file.exists()
    assert "Suite is reachable" in result.stdout


def test_service_start_reports_failure_when_runserver_exits(tmp_path: Path) -> None:
    base, _ = _prepare_service_start_tree(tmp_path)
    _write_python_stub(base / ".venv" / "bin" / "python", RUNSERVER_SOCKET_STUB)

    result = _run_service_start(
        base,
        extra_env={"FAIL_RUNSERVER": "1"},
        extra_args=["--await"],
    )

    assert result.returncode != 0
    assert "exited before readiness" in result.stdout
    assert "runserver failed" in result.stderr
