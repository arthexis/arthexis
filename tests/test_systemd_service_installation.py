import os
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX-compatible shell")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)


def _run_install(
    *,
    base_dir: Path,
    lock_dir: Path,
    systemd_dir: Path,
    exec_cmd: Path,
    enable_celery: bool,
    service_mode: str,
    stubs_dir: Path,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{stubs_dir}:{env['PATH']}"
    env["SYSTEMD_DIR"] = str(systemd_dir)
    command = textwrap.dedent(
        f"""
        set -e
        export SYSTEMD_DIR='{systemd_dir}'
        SUDO_CMD=()
        SYSTEMCTL_CMD=()
        . '{REPO_ROOT / 'scripts/helpers/systemd_locks.sh'}'
        umask 022
        arthexis_install_service_stack \
          '{base_dir}' \
          '{lock_dir}' \
          'gway' \
          {'true' if enable_celery else 'false'} \
          '{exec_cmd}' \
          '{service_mode}' \
          false
        """
    )
    return subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _prepare_stubs(stub_dir: Path) -> None:
    _write_executable(stub_dir / "sudo", "#!/usr/bin/env bash\n\nexec \"$@\"")
    _write_executable(stub_dir / "systemctl", "#!/usr/bin/env bash\n\nexit 0")


def test_install_service_writes_expected_units(tmp_path: Path) -> None:
    stubs_dir = tmp_path / "bin"
    stubs_dir.mkdir()
    _prepare_stubs(stubs_dir)

    base_dir = tmp_path / "svc"
    base_dir.mkdir()
    lock_dir = base_dir / "locks"
    lock_dir.mkdir()
    exec_cmd = base_dir / "service-start.sh"
    _write_executable(exec_cmd, "#!/usr/bin/env bash\necho start")

    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()

    result = _run_install(
        base_dir=base_dir,
        lock_dir=lock_dir,
        systemd_dir=systemd_dir,
        exec_cmd=exec_cmd,
        enable_celery=True,
        service_mode="systemd",
        stubs_dir=stubs_dir,
    )

    assert result.returncode == 0

    service_file = systemd_dir / "gway.service"
    celery_file = systemd_dir / "celery-gway.service"
    beat_file = systemd_dir / "celery-beat-gway.service"
    for file in (service_file, celery_file, beat_file):
        assert file.exists()
        assert (file.stat().st_mode & 0o777) == 0o644

    service_user = subprocess.check_output(["id", "-un"], text=True).strip()
    service_content = service_file.read_text(encoding="utf-8")
    assert "[Unit]" in service_content
    assert f"WorkingDirectory={base_dir}" in service_content
    assert f"ExecStart={exec_cmd}" in service_content
    assert f"User={service_user}" in service_content

    celery_content = celery_file.read_text(encoding="utf-8")
    assert f"WorkingDirectory={base_dir}" in celery_content
    assert "celery -A config worker" in celery_content
    assert f"User={service_user}" in celery_content

    beat_content = beat_file.read_text(encoding="utf-8")
    assert "celery -A config beat" in beat_content
    assert f"User={service_user}" in beat_content

    lock_file = lock_dir / "systemd_services.lck"
    assert lock_file.exists()
    recorded_units = lock_file.read_text(encoding="utf-8").splitlines()
    assert set(recorded_units) == {
        "gway.service",
        "celery-gway.service",
        "celery-beat-gway.service",
    }


def test_service_unit_names_include_optional_components() -> None:
    command = textwrap.dedent(
        f"""
        . '{REPO_ROOT / 'scripts/helpers/service_manager.sh'}'
        arthexis_service_unit_names 'gway' true true true
        """
    )

    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )

    units = set(result.stdout.splitlines())
    assert units == {
        "gway.service",
        "celery-gway.service",
        "celery-beat-gway.service",
        "lcd-gway.service",
        "gway-watchdog.service",
    }


def test_update_systemd_service_user_overwrites_and_inserts(tmp_path: Path) -> None:
    stubs_dir = tmp_path / "bin"
    stubs_dir.mkdir()
    _prepare_stubs(stubs_dir)

    base_dir = tmp_path / "svc"
    base_dir.mkdir()
    lock_dir = base_dir / "locks"
    lock_dir.mkdir()

    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()

    service_file = systemd_dir / "gway.service"
    service_file.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Test service",
                "[Service]",
                "WorkingDirectory=/tmp/old",
                "User=nobody",
                "[Install]",
                "WantedBy=multi-user.target",
            ]
        ),
        encoding="utf-8",
    )
    beat_file = systemd_dir / "celery-beat-gway.service"
    beat_file.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Beat",
                "[Service]",
                "WorkingDirectory=/tmp/old",
                "ExecStart=/bin/true",
                "[Install]",
                "WantedBy=multi-user.target",
            ]
        ),
        encoding="utf-8",
    )

    lock_file = lock_dir / "systemd_services.lck"
    lock_file.write_text(
        "\n".join(["gway.service", "celery-beat-gway.service"]) + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATH"] = f"{stubs_dir}:{env['PATH']}"
    env["SYSTEMD_DIR"] = str(systemd_dir)
    command = textwrap.dedent(
        f"""
        export SYSTEMD_DIR='{systemd_dir}'
        . '{REPO_ROOT / 'scripts/helpers/systemd_locks.sh'}'
        arthexis_update_systemd_service_user \
          '{base_dir}' \
          '{lock_dir}'
        """
    )

    subprocess.run(["bash", "-c", command], check=True, env=env, text=True)

    service_user = subprocess.check_output(["id", "-un"], text=True).strip()
    service_content = service_file.read_text(encoding="utf-8")
    assert f"User={service_user}" in service_content

    beat_content = beat_file.read_text(encoding="utf-8")
    assert f"User={service_user}" in beat_content
    assert beat_content.index("User=") > beat_content.index("[Service]")


def test_remove_service_unit_stack_clears_known_units(tmp_path: Path) -> None:
    stubs_dir = tmp_path / "bin"
    stubs_dir.mkdir()
    _prepare_stubs(stubs_dir)

    base_dir = tmp_path / "svc"
    base_dir.mkdir()
    lock_dir = base_dir / "locks"
    lock_dir.mkdir()

    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()

    unit_files = [
        systemd_dir / "gway.service",
        systemd_dir / "celery-gway.service",
        systemd_dir / "celery-beat-gway.service",
        systemd_dir / "lcd-gway.service",
        systemd_dir / "gway-watchdog.service",
    ]
    for file in unit_files:
        file.write_text("dummy", encoding="utf-8")

    lock_file = lock_dir / "systemd_services.lck"
    lock_file.write_text("\n".join([file.name for file in unit_files]), encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{stubs_dir}:{env['PATH']}"
    env["SYSTEMD_DIR"] = str(systemd_dir)

    command = textwrap.dedent(
        f"""
        export SYSTEMD_DIR='{systemd_dir}'
        PATH="{stubs_dir}:$PATH"
        . '{REPO_ROOT / 'scripts/helpers/service_manager.sh'}'
        arthexis_remove_service_unit_stack '{lock_dir}' 'gway' true true true
        """
    )

    subprocess.run(["bash", "-c", command], check=True, env=env, text=True)

    for file in unit_files:
        assert not file.exists()

    assert not lock_file.exists() or not lock_file.read_text(encoding="utf-8").strip()


def test_detect_service_mode_prefers_existing_systemd_units(tmp_path: Path) -> None:
    stubs_dir = tmp_path / "bin"
    stubs_dir.mkdir()
    _prepare_stubs(stubs_dir)

    base_dir = tmp_path / "svc"
    lock_dir = base_dir / "locks"
    lock_dir.mkdir(parents=True)
    service_name = "gway"
    (lock_dir / "service.lck").write_text(f"{service_name}\n", encoding="utf-8")

    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / f"{service_name}.service").write_text("[Unit]\n", encoding="utf-8")

    command = textwrap.dedent(
        f"""
        export SYSTEMD_DIR='{systemd_dir}'
        export PATH='{stubs_dir}':"$PATH"
        . '{REPO_ROOT / 'scripts/helpers/service_manager.sh'}'
        arthexis_detect_service_mode '{lock_dir}'
        """
    )

    result = subprocess.check_output(["bash", "-c", command], text=True).strip()
    assert result == "systemd"


@pytest.mark.parametrize(
    "unit_name",
    [
        "lcd-gway.service",
        "gway-watchdog.service",
    ],
)
def test_detect_service_mode_prefers_existing_systemd_dependents(
    tmp_path: Path, unit_name: str
) -> None:
    stubs_dir = tmp_path / "bin"
    stubs_dir.mkdir()
    _prepare_stubs(stubs_dir)

    base_dir = tmp_path / "svc"
    lock_dir = base_dir / "locks"
    lock_dir.mkdir(parents=True)
    service_name = "gway"
    (lock_dir / "service.lck").write_text(f"{service_name}\n", encoding="utf-8")

    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / unit_name).write_text("[Unit]\n", encoding="utf-8")

    command = textwrap.dedent(
        f"""
        export SYSTEMD_DIR='{systemd_dir}'
        export PATH='{stubs_dir}':"$PATH"
        . '{REPO_ROOT / 'scripts/helpers/service_manager.sh'}'
        arthexis_detect_service_mode '{lock_dir}'
        """
    )

    result = subprocess.check_output(["bash", "-c", command], text=True).strip()
    assert result == "systemd"
