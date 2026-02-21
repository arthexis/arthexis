"""Reusable health checks for OCPP."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from django.utils import timezone

from apps.core.system.ui import _build_nginx_report
from apps.nginx import config_utils
from apps.nodes.models import Node
from apps.ocpp.models import CPForwarder, Charger


def _format_timestamp(value) -> str:
    if not value:
        return "—"
    try:
        localized = timezone.localtime(value)
    except Exception:
        localized = value
    return localized.strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_bool(value: bool | None) -> str:
    if value is None:
        return "—"
    return "True" if value else "False"


def _format_list(values: list[str]) -> str:
    return ", ".join(values) if values else "—"


def _iter_websocket_urls(node: Node, path: str) -> list[str]:
    candidates: list[str] = []
    for url in node.iter_remote_urls(path):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        scheme = "wss" if parsed.scheme == "https" else "ws"
        candidates.append(urlunsplit((scheme, parsed.netloc, parsed.path, "", "")))
    return candidates


def _has_external_websocket_config(nginx_content: str) -> bool:
    return all(directive in nginx_content for directive in config_utils.websocket_directives())


def run_check_forwarders(*, stdout, **_kwargs) -> None:
    """Report on charge point forwarding configuration and activity."""

    local = Node.get_local()
    local_label = str(local) if local else "Unregistered"
    stdout.write(f"Local node: {local_label}")
    stdout.write("")
    stdout.write("Inbound forwarding readiness:")
    if not local:
        stdout.write("  Registered: False")
    else:
        host_candidates = local.get_remote_host_candidates(resolve_dns=False)
        metadata_urls = list(local.iter_remote_urls("/nodes/network/chargers/forward/"))
        ocpp_urls = _iter_websocket_urls(local, "/<charger_id>")
        ocpp_ws_urls = _iter_websocket_urls(local, "/ws/<charger_id>")
        nginx_report = _build_nginx_report()

        stdout.write("  Registered: True")
        stdout.write(f"  Preferred hosts: {_format_list(host_candidates)}")
        stdout.write(f"  Public endpoint slug: {local.public_endpoint or '—'}")
        stdout.write(f"  Public key configured: {_format_bool(bool(local.public_key))}")
        stdout.write(f"  Metadata endpoints: {_format_list(metadata_urls)}")
        stdout.write(f"  OCPP websocket endpoints: {_format_list(ocpp_urls)}")
        stdout.write(f"  OCPP websocket endpoints (/ws): {_format_list(ocpp_ws_urls)}")
        stdout.write("  Nginx configuration:")
        stdout.write(f"    Mode: {nginx_report.get('mode') or '—'}")
        stdout.write(f"    Backend port: {nginx_report.get('port') or '—'}")
        stdout.write(f"    Config path: {nginx_report.get('actual_path') or '—'}")
        stdout.write(
            "    External websockets enabled: "
            f"{_format_bool(nginx_report.get('external_websockets'))}"
        )
        if nginx_report.get("external_websockets"):
            actual_content = nginx_report.get("actual_content") or ""
            websocket_configured = _has_external_websocket_config(actual_content)
            stdout.write(f"    External websocket config: {_format_bool(websocket_configured)}")
        stdout.write(f"    Matches expected: {_format_bool(not nginx_report.get('differs'))}")
        expected_error = nginx_report.get("expected_error") or ""
        actual_error = nginx_report.get("actual_error") or ""
        if expected_error:
            stdout.write(f"    Expected config error: {expected_error}")
        if actual_error:
            stdout.write(f"    Actual config error: {actual_error}")
    stdout.write("")

    forwarders = list(
        CPForwarder.objects.select_related("source_node", "target_node").order_by(
            "target_node__hostname", "pk"
        )
    )
    stdout.write(f"Forwarders: {len(forwarders)}")
    if not forwarders:
        stdout.write("  (no forwarders configured)")
    for forwarder in forwarders:
        label = forwarder.name or f"Forwarder #{forwarder.pk}"
        source = str(forwarder.source_node) if forwarder.source_node else "Any"
        target = str(forwarder.target_node) if forwarder.target_node else "Unconfigured"
        stdout.write(f"- {label}")
        stdout.write(f"  Source: {source}")
        stdout.write(f"  Target: {target}")
        stdout.write(f"  Enabled: {forwarder.enabled}")
        stdout.write(f"  Running: {forwarder.is_running}")
        stdout.write(f"  Last synced: {_format_timestamp(forwarder.last_synced_at)}")
        stdout.write(
            f"  Last forwarded message: {_format_timestamp(forwarder.last_forwarded_at)}"
        )
        stdout.write(f"  Last status: {forwarder.last_status or '—'}")
        stdout.write(f"  Last error: {forwarder.last_error or '—'}")
        stdout.write(
            f"  Forwarded messages: {', '.join(forwarder.get_forwarded_messages()) or '—'}"
        )
        stdout.write("")

    chargers = list(
        Charger.objects.select_related("forwarded_to", "node_origin").order_by(
            "charger_id", "connector_id"
        )
    )
    forwarded_chargers = [charger for charger in chargers if charger.forwarded_to_id]
    exportable = [charger for charger in chargers if charger.export_transactions]

    stdout.write(f"Charge points: {len(chargers)}")
    stdout.write(f"  Export transactions enabled: {len(exportable)}")
    stdout.write(f"  Forwarded: {len(forwarded_chargers)}")
    if not chargers:
        stdout.write("  (no charge points configured)")
        return

    for charger in chargers:
        connector_label = f"#{charger.connector_id}" if charger.connector_id is not None else "main"
        origin = str(charger.node_origin) if charger.node_origin else "—"
        forwarded_to = str(charger.forwarded_to) if charger.forwarded_to else "—"
        stdout.write(f"- {charger.charger_id} ({connector_label})")
        stdout.write(f"  Origin: {origin}")
        stdout.write(f"  Forwarded to: {forwarded_to}")
        stdout.write(f"  Export transactions: {charger.export_transactions}")
        stdout.write(
            f"  Last forwarded message: {_format_timestamp(charger.forwarding_watermark)}"
        )
        stdout.write(f"  Last online: {_format_timestamp(charger.last_online_at)}")
        stdout.write("")
