"""Tests for the Windows/GWAY imager helper script."""

from __future__ import annotations

import sys
from contextlib import contextmanager
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


def test_windows_wlan_profile_xml_converts_wpa2_keyfile(tmp_path: Path) -> None:
    """Regression: saved Windows PSK profiles become NetworkManager keyfiles."""

    profile_xml = tmp_path / "Wi-Fi-IZZI.xml"
    profile_xml.write_text(
        """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>IZZI-158E-5G</name>
  <SSIDConfig>
    <SSID>
      <name>IZZI-158E-5G</name>
    </SSID>
  </SSIDConfig>
  <MSM>
    <security>
      <authEncryption>
        <authentication>WPA2PSK</authentication>
        <encryption>AES</encryption>
      </authEncryption>
      <sharedKey>
        <keyMaterial>example-password</keyMaterial>
      </sharedKey>
    </security>
  </MSM>
</WLANProfile>
""",
        encoding="utf-8",
    )

    profile = gway_imager.parse_windows_wlan_profile_xml(profile_xml)
    keyfile = gway_imager._networkmanager_keyfile_content(profile)

    assert profile.ssid == "IZZI-158E-5G"
    assert "[wifi-security]" in keyfile
    assert "key-mgmt=wpa-psk" in keyfile
    assert "psk=example-password" in keyfile


def test_windows_wlan_profile_xml_converts_open_keyfile(tmp_path: Path) -> None:
    """Regression: open saved profiles such as arthexis-1 do not need key material."""

    profile_xml = tmp_path / "Wi-Fi-arthexis-1.xml"
    profile_xml.write_text(
        """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>arthexis-1</name>
  <SSIDConfig>
    <SSID>
      <name>arthexis-1</name>
    </SSID>
  </SSIDConfig>
  <MSM>
    <security>
      <authEncryption>
        <authentication>open</authentication>
        <encryption>none</encryption>
      </authEncryption>
    </security>
  </MSM>
</WLANProfile>
""",
        encoding="utf-8",
    )

    profile = gway_imager.parse_windows_wlan_profile_xml(profile_xml)
    keyfile = gway_imager._networkmanager_keyfile_content(profile)

    assert profile.ssid == "arthexis-1"
    assert "ssid=arthexis-1" in keyfile
    assert "[wifi-security]" not in keyfile


def test_duplicate_windows_wlan_exports_accept_equivalent_profiles(tmp_path: Path) -> None:
    """Regression: netsh may export the same profile once per Wi-Fi interface."""

    profile_xml = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>arthexis-1</name>
  <SSIDConfig>
    <SSID>
      <name>arthexis-1</name>
    </SSID>
  </SSIDConfig>
  <MSM>
    <security>
      <authEncryption>
        <authentication>open</authentication>
        <encryption>none</encryption>
      </authEncryption>
    </security>
  </MSM>
</WLANProfile>
"""
    first = tmp_path / "Wi-Fi 2-arthexis-1.xml"
    second = tmp_path / "Wi-Fi 3-arthexis-1.xml"
    first.write_text(profile_xml, encoding="utf-8")
    second.write_text(profile_xml, encoding="utf-8")

    selected = gway_imager.select_exported_windows_wlan_profile("arthexis-1", [first, second])

    assert selected.ssid == "arthexis-1"


def test_create_burn_gway_builds_locally_uploads_and_runs_remote_writer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Regression: GWAY burns should use the local suite image and remote writer."""

    key_path = tmp_path / "recovery.pub"
    key_path.write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")
    monkeypatch.setenv("GWAY_IMAGER_RECOVERY_KEY_FILE", str(key_path))
    monkeypatch.setattr(gway_imager.shutil, "which", lambda command: "guestfish")
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


def test_create_burn_local_passes_selected_windows_wlan_profiles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Regression: selected Windows WLAN profiles are injected into build args."""

    key_path = tmp_path / "recovery.pub"
    key_path.write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")
    network_dir = tmp_path / "nm"
    network_dir.mkdir()
    monkeypatch.setenv("GWAY_IMAGER_RECOVERY_KEY_FILE", str(key_path))
    monkeypatch.setattr(gway_imager.shutil, "which", lambda command: "guestfish")

    @contextmanager
    def fake_windows_wlan_build_args(profile_names, *, runner):
        assert list(profile_names) == ["IZZI-158E-5G", "arthexis-1"]
        yield [
            "--host-network-profile-dir",
            str(network_dir),
            "--copy-host-network",
            "IZZI-158E-5G",
            "--copy-host-network",
            "arthexis-1",
        ]

    monkeypatch.setattr(gway_imager, "windows_wlan_build_args", fake_windows_wlan_build_args)
    runner = FakeRunner()

    exit_code = gway_imager.main(
        [
            "create-burn-local",
            "--name",
            "field",
            "--base-image-uri",
            "C:\\images\\raspios.img.xz",
            "--device",
            "\\\\.\\PhysicalDrive3",
            "--copy-windows-wlan-profile",
            "IZZI-158E-5G",
            "--copy-windows-wlan-profile",
            "arthexis-1",
            "--yes",
        ],
        repo_root=tmp_path,
        runner=runner,
    )

    assert exit_code == 0
    build_command = runner.commands[0][0]
    assert "--host-network-profile-dir" in build_command
    assert str(network_dir) in build_command
    assert build_command.count("--copy-host-network") == 2
    assert "IZZI-158E-5G" in build_command
    assert "arthexis-1" in build_command


def test_create_burn_local_fails_before_build_when_guestfish_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Regression: missing customization tools should block before write."""

    key_path = tmp_path / "recovery.pub"
    key_path.write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")
    monkeypatch.setenv("GWAY_IMAGER_RECOVERY_KEY_FILE", str(key_path))
    monkeypatch.setattr(gway_imager.shutil, "which", lambda command: None)
    runner = FakeRunner()

    exit_code = gway_imager.main(
        [
            "create-burn-local",
            "--name",
            "field",
            "--base-image-uri",
            "C:\\images\\raspios.img.xz",
            "--device",
            "\\\\.\\PhysicalDrive3",
            "--yes",
        ],
        repo_root=tmp_path,
        runner=runner,
    )

    assert exit_code == 2
    assert runner.commands == []
    assert "guestfish is required" in capsys.readouterr().err


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
