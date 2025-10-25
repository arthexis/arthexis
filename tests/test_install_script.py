from pathlib import Path
import re

import pytest


def test_install_script_runs_migrate():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "python manage.py migrate" in content


def test_install_script_includes_terminal_flag():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "--terminal" in content


def test_install_script_includes_constellation_flag():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "--constellation" in content


def test_install_script_excludes_virtual_flag():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "--virtual" not in content


def test_install_script_excludes_particle_flag():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "--particle" not in content







def test_install_script_runs_env_refresh():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "env-refresh.sh" in content


@pytest.mark.feature("nginx-server")
def test_install_script_requires_nginx_for_roles():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    expected_requirements = {
        "satellite": "satellite",
        "control": "control",
        "constellation": "watchtower",
    }
    for flag, requirement in expected_requirements.items():
        assert f"--{flag}" in content
        assert f'require_nginx "{requirement}"' in content


def test_install_script_role_defaults():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()

    def block(flag: str) -> str:
        pattern = rf"--{flag}\)(.*?)\n\s*;;"
        match = re.search(pattern, content, re.S)
        assert match, f"block for {flag} not found"
        return match.group(1)

    satellite = block("satellite")
    assert "AUTO_UPGRADE=true" in satellite
    assert "LATEST=false" in satellite

    constellation = block("constellation")
    assert "AUTO_UPGRADE=true" in constellation
    assert "LATEST=false" in constellation

    control = block("control")
    assert "AUTO_UPGRADE=true" in control
    assert "LATEST=true" in control

    assert "--virtual)" not in content
    assert "--particle)" not in content


def test_install_script_uses_auto_upgrade_lockfile():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "auto_upgrade.lck" in content


def test_install_script_checks_rfid_for_control_nodes():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "python -m ocpp.rfid.detect" in content
    assert "rfid-scanner" in content
