from django.test import override_settings

from apps.ocpp.transport import listener


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="eth0")
def test_scope_on_trusted_interface_address_is_trusted(monkeypatch) -> None:
    def addresses(interface: str) -> set[str]:
        return {"192.0.2.10"} if interface == "eth0" else set()

    monkeypatch.setattr(listener, "interface_addresses", addresses)

    assert listener.trusted_charger_listener({"server": ("192.0.2.10", 8888)})


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="eth0")
def test_scope_on_other_local_address_is_not_trusted(monkeypatch) -> None:
    def addresses(interface: str) -> set[str]:
        return {"192.0.2.10"} if interface == "eth0" else set()

    monkeypatch.setattr(listener, "interface_addresses", addresses)

    assert not listener.trusted_charger_listener(
        {"server": ("198.51.100.20", 8888)}
    )


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="")
def test_trusted_interface_can_be_disabled() -> None:
    assert not listener.trusted_charger_listener({"server": ("192.0.2.10", 8888)})
