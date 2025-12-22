from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.system import _build_nginx_report
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
    return all(
        directive in nginx_content
        for directive in config_utils.websocket_directives()
    )


class Command(BaseCommand):
    help = (
        "Report on charge point forwarding configuration and recent forwarding activity."
    )

    def handle(self, *args, **options) -> None:
        local = Node.get_local()
        local_label = str(local) if local else "Unregistered"
        self.stdout.write(f"Local node: {local_label}")
        self.stdout.write("")
        self.stdout.write("Inbound forwarding readiness:")
        if not local:
            self.stdout.write("  Registered: False")
        else:
            host_candidates = local.get_remote_host_candidates(resolve_dns=False)
            metadata_urls = list(
                local.iter_remote_urls("/nodes/network/chargers/forward/")
            )
            ocpp_urls = _iter_websocket_urls(local, "/<charger_id>")
            ocpp_ws_urls = _iter_websocket_urls(local, "/ws/<charger_id>")
            nginx_report = _build_nginx_report()

            self.stdout.write("  Registered: True")
            self.stdout.write(f"  Preferred hosts: {_format_list(host_candidates)}")
            self.stdout.write(
                f"  Public endpoint slug: {local.public_endpoint or '—'}"
            )
            self.stdout.write(
                f"  Public key configured: {_format_bool(bool(local.public_key))}"
            )
            self.stdout.write(
                "  Metadata endpoints: "
                f"{_format_list(metadata_urls)}"
            )
            self.stdout.write(
                "  OCPP websocket endpoints: "
                f"{_format_list(ocpp_urls)}"
            )
            self.stdout.write(
                "  OCPP websocket endpoints (/ws): "
                f"{_format_list(ocpp_ws_urls)}"
            )
            self.stdout.write("  Nginx configuration:")
            self.stdout.write(f"    Mode: {nginx_report.get('mode') or '—'}")
            self.stdout.write(f"    Backend port: {nginx_report.get('port') or '—'}")
            self.stdout.write(
                f"    Config path: {nginx_report.get('actual_path') or '—'}"
            )
            self.stdout.write(
                "    External websockets enabled: "
                f"{_format_bool(nginx_report.get('external_websockets'))}"
            )
            if nginx_report.get("external_websockets"):
                actual_content = nginx_report.get("actual_content") or ""
                websocket_configured = _has_external_websocket_config(actual_content)
                self.stdout.write(
                    "    External websocket config: "
                    f"{_format_bool(websocket_configured)}"
                )
            self.stdout.write(
                "    Matches expected: "
                f"{_format_bool(not nginx_report.get('differs'))}"
            )
            expected_error = nginx_report.get("expected_error") or ""
            actual_error = nginx_report.get("actual_error") or ""
            if expected_error:
                self.stdout.write(f"    Expected config error: {expected_error}")
            if actual_error:
                self.stdout.write(f"    Actual config error: {actual_error}")
        self.stdout.write("")

        forwarders = list(
            CPForwarder.objects.select_related("source_node", "target_node").order_by(
                "target_node__hostname", "pk"
            )
        )
        self.stdout.write(f"Forwarders: {len(forwarders)}")
        if not forwarders:
            self.stdout.write("  (no forwarders configured)")
        for forwarder in forwarders:
            label = forwarder.name or f"Forwarder #{forwarder.pk}"
            source = str(forwarder.source_node) if forwarder.source_node else "Any"
            target = str(forwarder.target_node) if forwarder.target_node else "Unconfigured"
            self.stdout.write(f"- {label}")
            self.stdout.write(f"  Source: {source}")
            self.stdout.write(f"  Target: {target}")
            self.stdout.write(f"  Enabled: {forwarder.enabled}")
            self.stdout.write(f"  Running: {forwarder.is_running}")
            self.stdout.write(
                f"  Last synced: {_format_timestamp(forwarder.last_synced_at)}"
            )
            self.stdout.write(
                f"  Last forwarded message: {_format_timestamp(forwarder.last_forwarded_at)}"
            )
            self.stdout.write(f"  Last status: {forwarder.last_status or '—'}")
            self.stdout.write(f"  Last error: {forwarder.last_error or '—'}")
            self.stdout.write(
                f"  Forwarded messages: {', '.join(forwarder.get_forwarded_messages()) or '—'}"
            )
            self.stdout.write("")

        chargers = list(
            Charger.objects.select_related("forwarded_to", "node_origin")
            .order_by("charger_id", "connector_id")
        )
        forwarded_chargers = [charger for charger in chargers if charger.forwarded_to_id]
        exportable = [charger for charger in chargers if charger.export_transactions]

        self.stdout.write(f"Charge points: {len(chargers)}")
        self.stdout.write(f"  Export transactions enabled: {len(exportable)}")
        self.stdout.write(f"  Forwarded: {len(forwarded_chargers)}")
        if not chargers:
            self.stdout.write("  (no charge points configured)")
            return

        for charger in chargers:
            connector_label = (
                f"#{charger.connector_id}" if charger.connector_id is not None else "main"
            )
            origin = str(charger.node_origin) if charger.node_origin else "—"
            forwarded_to = str(charger.forwarded_to) if charger.forwarded_to else "—"
            self.stdout.write(
                f"- {charger.charger_id} ({connector_label})"
            )
            self.stdout.write(f"  Origin: {origin}")
            self.stdout.write(f"  Forwarded to: {forwarded_to}")
            self.stdout.write(f"  Export transactions: {charger.export_transactions}")
            self.stdout.write(
                f"  Last forwarded message: {_format_timestamp(charger.forwarding_watermark)}"
            )
            self.stdout.write(
                f"  Last online: {_format_timestamp(charger.last_online_at)}"
            )
            self.stdout.write("")
