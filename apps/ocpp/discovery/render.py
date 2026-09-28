"""Human-readable rendering for durable charger discovery evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def render_discovery_events(
    session_id: str,
    events: Iterable[Mapping[str, object]],
) -> str:
    """Render operator-facing discovery text from the authoritative event stream."""

    lines = [f"Discovery session {session_id}"]
    candidates: list[Mapping[str, object]] = []

    for event in events:
        event_type = event.get("event_type")
        metadata = event.get("metadata")
        if not isinstance(metadata, Mapping):
            continue

        if event_type == "interface_selected":
            interface = metadata.get("interface") or "unknown"
            role = metadata.get("role") or "unknown"
            lines.append(f"Interface: {interface} ({role})")
        elif event_type == "dns_resolution":
            name = metadata.get("name")
            address = metadata.get("address")
            if name and address:
                lines.append(f"DNS: {name} -> {address}")
        elif event_type == "destination_mac_observed_without_resolution":
            endpoint = _endpoint(metadata)
            mac = metadata.get("destination_mac")
            if endpoint and mac:
                lines.append(
                    f"Layer 2: {endpoint} via {mac} (no fresh ARP observed)"
                )
        elif event_type == "websocket_upgrade":
            endpoint = _endpoint(metadata)
            path = metadata.get("path")
            host = metadata.get("hostname")
            subprotocol = metadata.get("subprotocol")
            details = [str(value) for value in (host, path, subprotocol) if value]
            if endpoint:
                suffix = f" [{', '.join(details)}]" if details else ""
                lines.append(f"WebSocket: {endpoint}{suffix}")
        elif event_type == "tls_client_hello":
            endpoint = _endpoint(metadata)
            sni = metadata.get("sni")
            if endpoint:
                suffix = f" [SNI {sni}]" if sni else ""
                lines.append(f"TLS: {endpoint}{suffix}")
        elif event_type == "csms_candidate":
            candidates.append(metadata)

    if candidates:
        lines.append("CSMS candidates:")
        for index, candidate in enumerate(candidates, start=1):
            endpoint = _endpoint(candidate) or "unknown"
            attempts = candidate.get("attempts", 0)
            details: list[str] = []
            hostnames = candidate.get("hostnames")
            if isinstance(hostnames, list) and hostnames:
                details.append("host=" + ",".join(str(item) for item in hostnames))
            paths = candidate.get("websocket_paths")
            if isinstance(paths, list) and paths:
                details.append("path=" + ",".join(str(item) for item in paths))
            protocols = candidate.get("ocpp_subprotocols")
            if isinstance(protocols, list) and protocols:
                details.append(
                    "protocol=" + ",".join(str(item) for item in protocols)
                )
            sni = candidate.get("tls_sni")
            if isinstance(sni, list) and sni:
                details.append("sni=" + ",".join(str(item) for item in sni))
            mac = candidate.get("destination_mac")
            if mac:
                details.append(f"mac={mac}")
            suffix = f" ({'; '.join(details)})" if details else ""
            lines.append(f"  {index}. {endpoint} attempts={attempts}{suffix}")
    else:
        lines.append("No CSMS candidates observed.")

    return "\n".join(lines)


def _endpoint(metadata: Mapping[str, object]) -> str | None:
    address = metadata.get("destination_ip")
    port = metadata.get("destination_port")
    if not address:
        return None
    return f"{address}:{port}" if port is not None else str(address)
