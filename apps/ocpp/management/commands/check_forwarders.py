from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

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


class Command(BaseCommand):
    help = (
        "Report on charge point forwarding configuration and recent forwarding activity."
    )

    def handle(self, *args, **options) -> None:
        local = Node.get_local()
        local_label = str(local) if local else "Unregistered"
        self.stdout.write(f"Local node: {local_label}")
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
