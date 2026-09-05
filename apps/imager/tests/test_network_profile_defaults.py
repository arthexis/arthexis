from pathlib import Path

import pytest

from apps.imager.services.models import ImagerBuildError
from apps.imager.services.network_profiles import (
    CHARGER_NETWORK_ADDRESS_ENV,
    DEFAULT_CHARGER_NETWORK_ADDRESS,
    DEFAULT_CHARGER_NETWORK_PROFILE_ID,
    DEFAULT_GWAY_PARENT_MANAGEMENT_ADDRESS,
    charger_network_host,
    select_host_network_profiles,
)


def test_default_charger_network_profile_is_injected(monkeypatch):
    monkeypatch.delenv(CHARGER_NETWORK_ADDRESS_ENV, raising=False)
    profiles = select_host_network_profiles()

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.name == DEFAULT_CHARGER_NETWORK_PROFILE_ID
    content = profile.source_path.read_text(encoding="utf-8")
    assert "interface-name=eth0" in content
    assert f"address1={DEFAULT_CHARGER_NETWORK_ADDRESS}" in content
    assert "never-default=true" in content
    assert charger_network_host() == "192.168.129.10"
    assert DEFAULT_GWAY_PARENT_MANAGEMENT_ADDRESS == "192.168.129.1/24"


def test_charger_network_address_can_be_overridden(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(CHARGER_NETWORK_ADDRESS_ENV, "192.168.129.20/24")

    profiles = select_host_network_profiles(generated_profile_dir=tmp_path)

    assert len(profiles) == 1
    content = profiles[0].source_path.read_text(encoding="utf-8")
    assert "address1=192.168.129.20/24" in content
    assert "address1=192.168.129.10/24" not in content
    assert charger_network_host() == "192.168.129.20"


def test_invalid_charger_network_address_is_rejected(monkeypatch):
    monkeypatch.setenv(CHARGER_NETWORK_ADDRESS_ENV, "not-an-address")

    with pytest.raises(ImagerBuildError, match="IPv4 interface address"):
        select_host_network_profiles()


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
