"""Tests for recovery AP namespace and per-device addressing."""

import pytest

from apps.imager.ap_addressing import AP_NAMESPACE_OCTETS, recovery_ap_address
from apps.imager.services.build_engine import _render_bootstrap_script


def test_gway_namespace_keeps_octet_42() -> None:
    assert AP_NAMESPACE_OCTETS["gway"] == 42


def test_gway_device_number_selects_third_octet() -> None:
    assert recovery_ap_address("gway", 4) == "10.42.4.1/24"
    assert recovery_ap_address("GWAY", 129) == "10.42.129.1/24"


def test_unknown_customer_namespace_must_be_allocated() -> None:
    with pytest.raises(ValueError, match="No recovery AP namespace is allocated"):
        recovery_ap_address("customer", 4)


@pytest.mark.parametrize("number", [0, 255])
def test_device_number_must_fit_ipv4_octet(number: int) -> None:
    with pytest.raises(ValueError, match="device number must be between 1 and 254"):
        recovery_ap_address("gway", number)


def test_bootstrap_prefers_reserved_recovery_ap_address() -> None:
    script = _render_bootstrap_script()

    assert 'local ap_address="${ARTHEXIS_RECOVERY_AP_ADDRESS:-}"' in script
    assert 'ipv4_args+=(ipv4.addresses "$ap_address")' in script
    assert '"${ipv4_args[@]}"' in script
    assert "ipv4_args+=(ipv4.addresses 10.42.0.1/16)" in script
