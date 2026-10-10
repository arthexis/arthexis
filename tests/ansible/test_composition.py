from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "ansible"
PLAYBOOKS = ANSIBLE / "playbooks"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_arthexis_playbook_remains_non_ocpp_application_component():
    content = (PLAYBOOKS / "arthexis.yml").read_text(encoding="utf-8")

    assert "Configure Arthexis" in content
    assert "ocpp_collector" not in content


def test_watchtower_composes_arthexis_and_ocpp_collector():
    content = (PLAYBOOKS / "watchtower.yml").read_text(encoding="utf-8")

    assert "import_playbook: arthexis.yml" in content
    assert "import_playbook: ocpp-collector.yml" in content


def test_ocpp_component_playbook_and_role_keep_ocpp_prefix():
    collector = _load(PLAYBOOKS / "ocpp-collector.yml")[0]
    assert collector["roles"] == [{"role": "ocpp_collector"}]
    assert (ANSIBLE / "roles" / "ocpp_collector").is_dir()


def test_appliance_playbook_delegates_ocpp_csms_to_its_own_checkout():
    content = (ANSIBLE / "appliance.yml").read_text(encoding="utf-8")

    assert "import_playbook: playbooks/arthexis.yml" in content
    assert "OCPP_CSMS_REPO" in content
    assert "ansible/playbooks/satellite.yml" in content
    assert "ansible-playbook" in content
    assert "chdir:" in content


def test_appliance_playbook_supports_explicit_ocpp_inventory():
    content = (ANSIBLE / "appliance.yml").read_text(encoding="utf-8")

    assert "OCPP_CSMS_INVENTORY" in content
    assert "ocpp_csms_inventory" in content


def test_default_inventory_targets_only_localhost():
    content = (ANSIBLE / "inventory.ini").read_text(encoding="utf-8")

    assert "localhost" in content
    assert "ansible_connection=local" in content


def test_ansible_config_uses_shared_roles_directory():
    content = (ROOT / "ansible.cfg").read_text(encoding="utf-8")

    assert "roles_path = ansible/roles" in content
