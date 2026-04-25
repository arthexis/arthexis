from __future__ import annotations

import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from apps.meta.models import Attention, WhatsAppChatBridge


CONNECTION_CLEANUP_INTERVAL = 60.0
ATTENTION_KEY_HELP = "Attention key"


class Command(BaseCommand):
    help = "Create, send, and wait for Attention requests."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action", required=True)

        ask = subparsers.add_parser("ask", help="Create an Attention request")
        ask.add_argument("message", help="Attention message body")
        ask.add_argument("--title", default="Attention", help="Attention title")
        ask.add_argument("--agent", default=os.environ.get("USER", ""), help="Agent name")
        ask.add_argument("--severity", default="urgent", help="Attention severity")
        ask.add_argument("--recipient", default="", help="WhatsApp recipient phone number")
        ask.add_argument("--bridge", type=int, default=None, help="WhatsApp bridge primary key")
        ask.add_argument(
            "--no-send",
            action="store_true",
            help="Create the Attention without sending WhatsApp",
        )
        ask.add_argument("--wait", action="store_true", help="Wait until a response arrives")
        ask.add_argument(
            "--timeout", type=float, default=0, help="Seconds to wait; 0 means forever"
        )
        ask.add_argument("--poll-interval", type=float, default=1.0, help="Wait polling interval")

        wait = subparsers.add_parser("wait", help="Wait for an Attention response")
        wait.add_argument("key", help=ATTENTION_KEY_HELP)
        wait.add_argument(
            "--timeout", type=float, default=0, help="Seconds to wait; 0 means forever"
        )
        wait.add_argument("--poll-interval", type=float, default=1.0, help="Wait polling interval")

        show = subparsers.add_parser("show", help="Show an Attention request")
        show.add_argument("key", help=ATTENTION_KEY_HELP)

        respond = subparsers.add_parser("respond", help="Record a manual Attention response")
        respond.add_argument("key", help=ATTENTION_KEY_HELP)
        respond.add_argument("message", help="Response message")
        respond.add_argument("--from-phone", default="", help="Response sender phone")
        respond.add_argument(
            "--force",
            action="store_true",
            help="Overwrite an existing Attention response explicitly",
        )

    def handle(self, *args, **options):
        action = options["action"]
        if action == "ask":
            return self._handle_ask(options)
        if action == "wait":
            return self._handle_wait(options["key"], options)
        if action == "show":
            return self._handle_show(options["key"])
        if action == "respond":
            return self._handle_respond(options)
        raise CommandError(f"Unknown attention action: {action}")

    def _resolve_bridge(self, bridge_id: int | None):
        if bridge_id is not None:
            try:
                return WhatsAppChatBridge.objects.get(pk=bridge_id)
            except WhatsAppChatBridge.DoesNotExist as exc:
                raise CommandError(f"WhatsApp bridge not found: {bridge_id}") from exc
        return WhatsAppChatBridge.objects.for_site(None)

    def _resolve_recipient(self, explicit_recipient: str) -> str:
        return (
            explicit_recipient
            or getattr(settings, "ATTENTION_WHATSAPP_RECIPIENT", "")
            or os.environ.get("ATTENTION_WHATSAPP_RECIPIENT", "")
        ).strip()

    def _handle_ask(self, options):
        recipient = self._resolve_recipient(options["recipient"])
        if options["no_send"]:
            bridge = (
                self._resolve_bridge(options["bridge"])
                if options["bridge"] is not None
                else None
            )
        else:
            bridge = self._resolve_bridge(options["bridge"])
        attention = Attention.objects.create(
            bridge=bridge,
            recipient=recipient,
            agent=options["agent"],
            severity=options["severity"],
            title=options["title"],
            message=options["message"],
        )
        sent = False
        if not options["no_send"]:
            sent = attention.send()
            if not sent:
                self.stderr.write(
                    self.style.WARNING(
                        "Attention was created but WhatsApp delivery did not report success."
                    )
                )
        self.stdout.write(f"attention={attention.key}")
        self.stdout.write(f"status={attention.status}")
        self.stdout.write(f"sent={'yes' if sent else 'no'}")
        if options["wait"]:
            if not sent:
                raise CommandError(
                    f"Attention {attention.key} was not delivered; refusing to wait."
                )
            return self._handle_wait(attention.key, options)
        return None

    def _handle_wait(self, key: str, options):
        attention = self._get_attention(key)
        deadline = time.monotonic() + options["timeout"] if options["timeout"] else None
        poll_interval = max(float(options["poll_interval"]), 0.1)
        last_connection_cleanup = 0.0
        while True:
            now = time.monotonic()
            if now - last_connection_cleanup >= CONNECTION_CLEANUP_INTERVAL:
                close_old_connections()
                last_connection_cleanup = now
            attention.refresh_from_db()
            if attention.status == Attention.Status.RESPONDED:
                self.stdout.write(f"attention={attention.key}")
                self.stdout.write(f"response_from={attention.response_from_phone}")
                self.stdout.write(f"response={attention.response_text}")
                return None
            if attention.status == Attention.Status.CANCELLED:
                raise CommandError(f"Attention {attention.key} was cancelled")
            if deadline is not None and time.monotonic() >= deadline:
                raise CommandError(f"Timed out waiting for Attention {attention.key}")
            time.sleep(poll_interval)

    def _handle_show(self, key: str):
        attention = self._get_attention(key)
        self.stdout.write(f"attention={attention.key}")
        self.stdout.write(f"status={attention.status}")
        self.stdout.write(f"title={attention.title}")
        self.stdout.write(f"agent={attention.agent}")
        self.stdout.write(f"recipient={attention.recipient}")
        if attention.response_text:
            self.stdout.write(f"response_from={attention.response_from_phone}")
            self.stdout.write(f"response={attention.response_text}")
        return None

    def _handle_respond(self, options):
        attention = self._get_attention(options["key"])
        if attention.status == Attention.Status.RESPONDED and not options["force"]:
            raise CommandError(
                f"Attention {attention.key} already has a response; use --force to overwrite."
            )
        attention.mark_responded(
            response_text=options["message"],
            response_from_phone=options["from_phone"],
            response_payload={"source": "manual"},
        )
        self.stdout.write(f"attention={attention.key}")
        self.stdout.write(f"response={attention.response_text}")
        return None

    def _get_attention(self, key: str) -> Attention:
        try:
            return Attention.objects.get(key__iexact=key)
        except Attention.DoesNotExist as exc:
            raise CommandError(f"Attention not found: {key}") from exc
