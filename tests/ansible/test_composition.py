from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "ansible"


def test_site_playbook_remains_arthexis_only():
    content = (ANSIBLE / "site.yml").read_text()

    assert "Configure Arthexis" in content
    assert "ocpp" not in content.lower()


def test_appliance_playbook_delegates_ocpp_csms_to_its_own_checkout():
    content = (ANSIBLE / "appliance.yml").read_text()

    assert "import_playbook: site.yml" in content
    assert "OCPP_CSMS_REPO" in content
    assert "ansible/playbooks/satellite.yml" in content
    assert "ansible-playbook" in content
    assert "chdir:" in content


def test_appliance_playbook_supports_explicit_ocpp_inventory():
    content = (ANSIBLE / "appliance.yml").read_text()

    assert "OCPP_CSMS_INVENTORY" in content
    assert "ocpp_csms_inventory" in content


def test_default_inventory_targets_only_localhost():
    content = (ANSIBLE / "inventory.ini").read_text()

    assert "localhost" in content
    assert "ansible_connection=local" in content
