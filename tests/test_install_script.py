from pathlib import Path
import re

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


def test_install_script_includes_virtual_flag():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "--virtual" in content


def test_install_script_includes_particle_flag():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "--particle" in content


def test_install_script_runs_env_refresh():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    assert "env-refresh.sh" in content


def test_install_script_requires_nginx_for_roles():
    script_path = Path(__file__).resolve().parent.parent / "install.sh"
    content = script_path.read_text()
    for role in ("satellite", "control", "constellation", "virtual"):
        assert f'require_nginx "{role}"' in content


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

    virtual = block("virtual")
    assert "AUTO_UPGRADE=true" in virtual
    assert "LATEST=false" in virtual

    particle = block("particle")
    assert "AUTO_UPGRADE=false" in particle
    assert "LATEST=true" in particle

