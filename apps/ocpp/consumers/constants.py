from enum import StrEnum


class OCPPVersion(StrEnum):
    """Canonical OCPP protocol versions supported by the CSMS."""

    V16 = "ocpp1.6"
    V201 = "ocpp2.0.1"
    V21 = "ocpp2.1"


# Keep the established constant names as string-compatible enum aliases so
# existing call sites and persisted CharField values continue to work.
OCPP_VERSION_16 = OCPPVersion.V16
OCPP_SUBPROTOCOL_16J = "ocpp1.6j"
OCPP_VERSION_201 = OCPPVersion.V201
OCPP_VERSION_21 = OCPPVersion.V21

OCPP_CONNECT_RATE_LIMIT_FALLBACK = 1
OCPP_CONNECT_RATE_LIMIT_WINDOW_SECONDS = 2

# Query parameter keys that may contain the charge point serial. Keys are
# matched case-insensitively and trimmed before use.
SERIAL_QUERY_PARAM_NAMES = (
    "cid",
    "chargepointid",
    "charge_point_id",
    "chargeboxid",
    "charge_box_id",
    "chargerid",
)
