from apps.ocpp.consumers import (
    OCPP_VERSION_16,
    OCPP_VERSION_21,
    OCPP_VERSION_201,
    OCPPVersion,
)
from apps.ocpp.consumers.connection import SubprotocolConnectionMixin


def test_ocpp_version_constants_are_string_compatible_enum_members():
    assert OCPP_VERSION_16 is OCPPVersion.V16
    assert OCPP_VERSION_201 is OCPPVersion.V201
    assert OCPP_VERSION_21 is OCPPVersion.V21
    assert str(OCPPVersion.V16) == "ocpp1.6"
    assert OCPPVersion.V201 == "ocpp2.0.1"


def test_subprotocol_canonicalization_returns_ocpp_version():
    mixin = SubprotocolConnectionMixin()

    assert mixin._canonicalize_ocpp_subprotocol("ocpp1.6") is OCPPVersion.V16
    assert mixin._canonicalize_ocpp_subprotocol("ocpp1.6j") is OCPPVersion.V16
    assert mixin._canonicalize_ocpp_subprotocol("ocpp2.0.1") is OCPPVersion.V201
    assert mixin._canonicalize_ocpp_subprotocol("ocpp2.1") is OCPPVersion.V21
    assert mixin._canonicalize_ocpp_subprotocol("vendor-ocpp") is None


def test_subprotocol_selection_preserves_wire_token():
    mixin = SubprotocolConnectionMixin()

    selected_16 = mixin._select_subprotocol(["ocpp1.6", "ocpp1.6j"], None)
    assert selected_16 == "ocpp1.6j"
    assert mixin._canonicalize_ocpp_subprotocol(selected_16) is OCPPVersion.V16

    selected_21 = mixin._select_subprotocol(["ocpp1.6", "ocpp2.1"], None)
    assert selected_21 == "ocpp2.1"
    assert mixin._canonicalize_ocpp_subprotocol(selected_21) is OCPPVersion.V21
