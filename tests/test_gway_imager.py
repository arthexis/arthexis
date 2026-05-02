"""Tests for the Windows/GWAY imager helper script."""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import gway_imager


class FakeRunner(gway_imager.CommandRunner):
    """Record commands and synthesize build output for create-and-burn tests."""

    def __init__(self) -> None:
        self.commands: list[tuple[list[str], Path | None]] = []

    def run(self, command, *, cwd=None) -> None:
        command_list = [str(part) for part in command]
        self.commands.append((command_list, cwd))
        if "manage.py" in command_list and "build" in command_list:
            name = _option_value(command_list, "--name")
            output_dir = _option_value(command_list, "--output-dir") or gway_imager.DEFAULT_OUTPUT_DIR
            output_path = gway_imager.output_image_path(Path(cwd), output_dir=output_dir, name=name)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"rpi-image")


def _option_value(command: list[str], option: str) -> str:
    for index, value in enumerate(command):
        if value == option:
            return command[index + 1]
        if value.startswith(f"{option}="):
            return value.split("=", 1)[1]
    return ""


def test_enrich_build_args_adds_local_suite_and_default_recovery_key(tmp_path: Path) -> None:
    """Regression: the helper should make the safe build path the default."""

    key_path = tmp_path / "id_ed25519.pub"
    key_path.write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")

    args = gway_imager.enrich_build_args(
        ["--name", "field", "--base-image-uri", "raspios.img.xz"],
        repo_root=tmp_path,
        recovery_key_file=key_path,
    )

    assert args[-4:] == [
        "--suite-source",
        str(tmp_path),
        "--recovery-authorized-key-file",
        str(key_path),
    ]


def test_enrich_build_args_respects_explicit_recovery_skip(tmp_path: Path) -> None:
    """Regression: explicit operator opt-out must remain possible."""

    args = gway_imager.enrich_build_args(
        ["--name", "field", "--base-image-uri", "raspios.img.xz", "--skip-recovery-ssh"],
        repo_root=tmp_path,
    )

    assert "--suite-source" in args
    assert "--recovery-authorized-key-file" not in args


def test_create_burn_gway_builds_locally_uploads_and_runs_remote_writer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Regression: GWAY burns should use the local suite image and remote writer."""

    key_path = tmp_path / "recovery.pub"
    key_path.write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")
    monkeypatch.setenv("GWAY_IMAGER_RECOVERY_KEY_FILE", str(key_path))
    runner = FakeRunner()

    exit_code = gway_imager.main(
        [
            "create-burn-gway",
            "--name",
            "field",
            "--base-image-uri",
            "C:\\images\\raspios.img.xz",
            "--device",
            "/dev/sdb",
            "--yes",
        ],
        repo_root=tmp_path,
        runner=runner,
    )

    assert exit_code == 0
    build_command = runner.commands[0][0]
    assert build_command[:3] == [sys.executable, "manage.py", "imager"]
    assert build_command[3:5] == ["build", "--name"]
    assert "--suite-source" in build_command
    assert str(tmp_path) in build_command
    assert "--recovery-authorized-key-file" in build_command
    assert str(key_path) in build_command

    assert runner.commands[1][0][:2] == ["ssh", gway_imager.DEFAULT_GWAY_TARGET]
    assert runner.commands[2][0][0] == "scp"
    assert runner.commands[2][0][2].endswith(":/tmp/arthexis-imager/field-rpi-4b.img")
    remote_write = runner.commands[3][0]
    assert remote_write[:2] == ["ssh", gway_imager.DEFAULT_GWAY_TARGET]
    assert "manage.py imager write" in remote_write[2]
    assert "--image-path /tmp/arthexis-imager/field-rpi-4b.img" in remote_write[2]
    assert "--device /dev/sdb --yes" in remote_write[2]


def test_burn_local_delegates_to_suite_writer(tmp_path: Path) -> None:
    """Regression: local burns should stay inside the suite writer command."""

    runner = FakeRunner()

    exit_code = gway_imager.main(
        [
            "burn-local",
            "--artifact",
            "field",
            "--device",
            "\\\\.\\PhysicalDrive3",
            "--yes",
        ],
        repo_root=tmp_path,
        runner=runner,
    )

    assert exit_code == 0
    assert runner.commands == [
        (
            [
                sys.executable,
                "manage.py",
                "imager",
                "write",
                "--artifact",
                "field",
                "--device",
                "\\\\.\\PhysicalDrive3",
                "--yes",
            ],
            tmp_path,
        )
    ]
