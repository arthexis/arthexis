import ipaddress
import re
from urllib.parse import parse_qs

from django.conf import settings
from django.core.exceptions import ValidationError

from ... import store
from ...models import Charger
from ..constants import SERIAL_QUERY_PARAM_NAMES

FORWARDED_PAIR_RE = re.compile(r"for=(?:\"?)(?P<value>[^;,\"\s]+)(?:\"?)", re.IGNORECASE)


def _extract_vehicle_identifier(payload: dict) -> tuple[str, str]:
    """Return normalized VID and VIN values from an OCPP message payload."""

    raw_vid = payload.get("vid")
    vid_value = str(raw_vid).strip() if raw_vid is not None else ""
    raw_vin = payload.get("vin")
    vin_value = str(raw_vin).strip() if raw_vin is not None else ""
    if not vid_value and vin_value:
        vid_value = vin_value
    return vid_value, vin_value


def _register_log_names_for_identity(
    charger_id: str, connector_id: int | str | None, display_name: str
) -> None:
    """Register friendly log names for a charger identity and its pending key."""

    if not charger_id:
        return
    friendly_name = display_name or charger_id
    store.register_log_name(
        store.identity_key(charger_id, connector_id),
        friendly_name,
        log_type="charger",
    )
    if connector_id is None:
        store.register_log_name(
            store.pending_key(charger_id), friendly_name, log_type="charger"
        )
        store.register_log_name(charger_id, friendly_name, log_type="charger")


def _parse_ip(value: str | None):
    """Return an :mod:`ipaddress` object for the provided value, if valid."""

    candidate = (value or "").strip()
    if not candidate or candidate.lower() == "unknown":
        return None
    if candidate.lower().startswith("for="):
        candidate = candidate[4:].strip()
    candidate = candidate.strip("'\"")
    if candidate.startswith("["):
        closing = candidate.find("]")
        if closing != -1:
            candidate = candidate[1:closing]
        else:
            candidate = candidate[1:]
    # Remove any comma separated values that may remain.
    if "," in candidate:
        candidate = candidate.split(",", 1)[0].strip()
    try:
        parsed = ipaddress.ip_address(candidate)
    except ValueError:
        host, sep, maybe_port = candidate.rpartition(":")
        if not sep or not maybe_port.isdigit():
            return None
        try:
            parsed = ipaddress.ip_address(host)
        except ValueError:
            return None
    return parsed


def _get_trusted_proxy_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    """Return configured trusted proxy networks for client IP resolution."""

    raw_value = getattr(settings, "OCPP_TRUSTED_PROXY_IPS", None)
    if not raw_value:
        return ()
    if isinstance(raw_value, str):
        raw_values = [entry.strip() for entry in raw_value.split(",") if entry.strip()]
    else:
        raw_values = [str(entry).strip() for entry in raw_value if str(entry).strip()]
    networks: list[ipaddress._BaseNetwork] = []
    for entry in raw_values:
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _is_trusted_proxy(client_ip: ipaddress._BaseAddress) -> bool:
    """Return True when the client IP is a configured trusted proxy."""

    for network in _get_trusted_proxy_networks():
        if client_ip in network:
            return True
    return False


def _resolve_client_ip(scope: dict) -> str | None:
    """Return the most useful client IP for the provided ASGI scope."""

    client = scope.get("client")
    connection_ip = _parse_ip((client[0] or "").strip()) if client else None
    if not connection_ip:
        return None

    if not _is_trusted_proxy(connection_ip):
        return str(connection_ip)

    headers = scope.get("headers") or []
    header_map: dict[str, list[str]] = {}
    for key_bytes, value_bytes in headers:
        try:
            key = key_bytes.decode("latin1").lower()
        except Exception:
            continue
        try:
            value = value_bytes.decode("latin1")
        except Exception:
            value = ""
        header_map.setdefault(key, []).append(value)

    candidates: list[str] = []
    for raw in header_map.get("x-forwarded-for", []):
        candidates.extend(part.strip() for part in raw.split(","))
    if not candidates:
        for raw in header_map.get("forwarded", []):
            for segment in raw.split(","):
                match = FORWARDED_PAIR_RE.search(segment)
                if match:
                    candidates.append(match.group("value"))
    if not candidates:
        candidates.extend(header_map.get("x-real-ip", []))

    chain: list[ipaddress._BaseAddress] = []
    for raw in candidates:
        parsed = _parse_ip(raw)
        if parsed:
            chain.append(parsed)
    chain.append(connection_ip)

    for parsed in reversed(chain):
        if not _is_trusted_proxy(parsed):
            return str(parsed)
    return str(connection_ip)


class IdentityMixin:
    def _extract_serial_identifier(self) -> str:
        """Return the charge point serial from the query string or path."""

        self.serial_source = None
        query_bytes = self.scope.get("query_string") or b""
        self._raw_query_string = query_bytes.decode("utf-8", "ignore") if query_bytes else ""
        if query_bytes:
            try:
                parsed = parse_qs(
                    self._raw_query_string,
                    keep_blank_values=False,
                )
            except Exception:
                parsed = {}
            if parsed:
                normalized = {
                    key.lower(): values for key, values in parsed.items() if values
                }
                for candidate in SERIAL_QUERY_PARAM_NAMES:
                    values = normalized.get(candidate)
                    if not values:
                        continue
                    for value in values:
                        if not value:
                            continue
                        trimmed = value.strip()
                        if trimmed:
                            self.serial_source = "query"
                            return trimmed

        serial = self.scope["url_route"]["kwargs"].get("cid", "").strip()
        if serial:
            self.serial_source = "route"
            return serial

        path = (self.scope.get("path") or "").strip("/")
        if not path:
            return ""

        segments = [segment for segment in path.split("/") if segment]
        if not segments:
            return ""

        serial = segments[-1].strip()
        if not serial:
            return ""
        self.serial_source = "path"
        return serial

    async def _validate_serial_or_reject(self, raw_serial: str) -> bool:
        """Validate the charge point serial and reject the connection if invalid."""

        try:
            self.charger_id = Charger.validate_serial(raw_serial)
        except ValidationError as exc:
            serial = Charger.normalize_serial(raw_serial)
            store_key = store.pending_key(serial)
            message = exc.messages[0] if exc.messages else "Invalid Serial Number"
            details: list[str] = []
            if getattr(self, "serial_source", None):
                details.append(f"serial_source={self.serial_source}")
            if getattr(self, "_raw_query_string", ""):
                details.append(f"query_string={self._raw_query_string!r}")
            if details:
                message = f"{message} ({'; '.join(details)})"
            store.add_log(
                store_key,
                f"Rejected connection: {message}",
                log_type="charger",
            )
            await self.close(code=4003)
            return False
        return True


__all__ = [
    "FORWARDED_PAIR_RE",
    "IdentityMixin",
    "_extract_vehicle_identifier",
    "_get_trusted_proxy_networks",
    "_is_trusted_proxy",
    "_parse_ip",
    "_register_log_names_for_identity",
    "_resolve_client_ip",
]
