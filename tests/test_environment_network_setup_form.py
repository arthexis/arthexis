from __future__ import annotations

from core.environment import NetworkSetupForm


def build_form(**overrides: str) -> NetworkSetupForm:
    data = {"vnc_validation": "default"}
    data.update(overrides)
    return NetworkSetupForm(data=data)


def test_network_setup_form_accepts_last_octet_subnet() -> None:
    form = build_form(ethernet_subnet="42")
    assert form.is_valid()
    assert form.cleaned_data["ethernet_subnet"] == "42"


def test_network_setup_form_accepts_full_subnet_without_prefix() -> None:
    form = build_form(ethernet_subnet="10.20.30")
    assert form.is_valid()
    assert form.cleaned_data["ethernet_subnet"] == "10.20.30"


def test_network_setup_form_accepts_full_subnet_with_prefix() -> None:
    form = build_form(ethernet_subnet="10.20.30/24")
    assert form.is_valid()
    assert form.cleaned_data["ethernet_subnet"] == "10.20.30/24"


def test_network_setup_form_rejects_incomplete_subnet() -> None:
    form = build_form(ethernet_subnet="10.20")
    assert not form.is_valid()
    assert "ethernet_subnet" in form.errors


def test_network_setup_form_rejects_third_octet_overflow() -> None:
    form = build_form(ethernet_subnet="10.20.255")
    assert not form.is_valid()
    assert "ethernet_subnet" in form.errors
