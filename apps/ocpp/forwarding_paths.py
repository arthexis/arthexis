from __future__ import annotations

from typing import Final

FORWARDING_WEBSOCKET_PREFIXES: Final[tuple[str, ...]] = (
    "/ocpp",
    "/ws/ocpp",
    "",
    "/ws",
)

FORWARDING_WEBSOCKET_PATHS: Final[tuple[str, ...]] = (
    "/ocpp/<charger_id>",
    "/ws/ocpp/<charger_id>",
)

LEGACY_FORWARDING_WEBSOCKET_PATHS: Final[tuple[str, ...]] = (
    "/ws/<charger_id>",
    "/<charger_id>",
)
