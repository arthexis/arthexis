import ipaddress
import re

FORWARDED_PAIR_RE = re.compile(r"for=(?:\"?)(?P<value>[^;,\"\s]+)(?:\"?)", re.IGNORECASE)


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


def _resolve_client_ip(scope: dict) -> str | None:
    """Return the most useful client IP for the provided ASGI scope."""

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
    for raw in header_map.get("forwarded", []):
        for segment in raw.split(","):
            match = FORWARDED_PAIR_RE.search(segment)
            if match:
                candidates.append(match.group("value"))
    candidates.extend(header_map.get("x-real-ip", []))
    client = scope.get("client")
    if client:
        candidates.append((client[0] or "").strip())

    fallback: str | None = None
    for raw in candidates:
        parsed = _parse_ip(raw)
        if not parsed:
            continue
        ip_text = str(parsed)
        if parsed.is_loopback:
            if fallback is None:
                fallback = ip_text
            continue
        return ip_text
    return fallback
