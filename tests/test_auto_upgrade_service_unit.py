import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX-compatible shell")


def _run_repair(base_dir: Path, systemd_dir: Path, service: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    command = " ".join(
        [
            "SUDO_CMD=()",  # ensure helper skips sudo
            "SYSTEMCTL_CMD=()",  # skip systemctl invocations
            f"SYSTEMD_DIR='{systemd_dir}'",
            f". '{REPO_ROOT / 'scripts/helpers/auto-upgrade-service.sh'}'",
            "&&",
            f"arthexis_repair_auto_upgrade_workdir '{base_dir}' '{service}' '{systemd_dir}'",
        ]
    )
    return subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def test_repair_updates_existing_working_directory(tmp_path: Path) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()

    service = "gway"
    unit_file = systemd_dir / f"{service}-auto-upgrade.service"
    unit_file.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Auto-upgrade service",
                "[Service]",
                "WorkingDirectory=/missing/path",
                "ExecStart=/usr/bin/env true",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_repair(tmp_path, systemd_dir, service)

    assert result.returncode == 0
    content = unit_file.read_text(encoding="utf-8")
    assert f"WorkingDirectory={tmp_path}" in content
    assert "WorkingDirectory=/missing/path" not in content


def test_repair_inserts_working_directory_when_missing(tmp_path: Path) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()

    service = "gway"
    unit_file = systemd_dir / f"{service}-auto-upgrade.service"
    unit_file.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Auto-upgrade service",
                "[Service]",
                "ExecStart=/usr/bin/env true",
            ]
        ),
        encoding="utf-8",
    )

    result = _run_repair(tmp_path, systemd_dir, service)

    assert result.returncode == 0
    content = unit_file.read_text(encoding="utf-8")
    assert f"WorkingDirectory={tmp_path}" in content
    assert content.index("WorkingDirectory") > content.index("[Service]")
