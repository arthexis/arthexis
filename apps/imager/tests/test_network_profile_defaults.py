from pathlib import Path

from apps.imager.services.network_profiles import (
    DEFAULT_CHARGER_NETWORK_PROFILE_ID,
    select_host_network_profiles,
)


def test_default_charger_network_profile_is_injected():
    profiles = select_host_network_profiles()

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.name == DEFAULT_CHARGER_NETWORK_PROFILE_ID
    content = profile.source_path.read_text(encoding="utf-8")
    assert "interface-name=eth0" in content
    assert "address1=192.168.129.10/24" in content
    assert "never-default=true" in content


def test_explicit_eth0_profile_replaces_default(tmp_path: Path):
    override = tmp_path / "field-eth0.nmconnection"
    override.write_text(
        """[connection]\nid=field-eth0\ntype=ethernet\ninterface-name=eth0\n\n[ipv4]\naddress1=192.168.129.20/24\nmethod=manual\n""",
        encoding="utf-8",
    )

    profiles = select_host_network_profiles(
        profile_dir=tmp_path,
        names=["field-eth0"],
    )

    assert [profile.name for profile in profiles] == ["field-eth0"]
    assert profiles[0].source_path == override
