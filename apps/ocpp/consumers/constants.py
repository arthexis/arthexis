OCPP_VERSION_16 = "ocpp1.6"
OCPP_VERSION_201 = "ocpp2.0.1"
OCPP_VERSION_21 = "ocpp2.1"

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
