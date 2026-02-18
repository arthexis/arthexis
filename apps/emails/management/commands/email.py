"""Management command for reporting and operating email profiles."""

from __future__ import annotations

import json
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.emails import mailer
from apps.emails.models import EmailBridge, EmailInbox, EmailOutbox


class Command(BaseCommand):
    """Report and manage email inbox/outbox/bridge profiles from the CLI."""

    INBOX_EDITABLE_KEYS = {
        "inbox_username",
        "inbox_host",
        "inbox_port",
        "inbox_protocol",
        "inbox_password",
        "inbox_priority",
        "inbox_use_ssl",
        "inbox_no_ssl",
        "inbox_enabled",
        "inbox_disabled",
    }
    OUTBOX_EDITABLE_KEYS = {
        "outbox_host",
        "outbox_port",
        "outbox_username",
        "outbox_password",
        "outbox_from_email",
        "outbox_priority",
        "outbox_use_tls",
        "outbox_no_tls",
        "outbox_use_ssl",
        "outbox_no_ssl",
        "outbox_enabled",
        "outbox_disabled",
    }
    BRIDGE_EDITABLE_KEYS = {"bridge", "bridge_name", "bridge_inbox", "bridge_outbox"}

    help = (
        "Report email profile configuration, configure inbox/outbox/bridge records, "
        "send outbound emails, and search inbound emails."
    )

    def add_arguments(self, parser) -> None:
        """Register CLI flags for reporting, configuration, sending, and searching."""

        parser.add_argument("--inbox", type=int, help="Inbox id to update or use for search.")
        parser.add_argument("--outbox", type=int, help="Outbox id to update or use for send.")
        parser.add_argument("--bridge", type=int, help="Bridge id to update.")

        parser.add_argument("--owner-user", type=int, help="Owner user id for created/updated profiles.")

        parser.add_argument("--inbox-username", help="Inbox login username.")
        parser.add_argument("--inbox-host", help="Inbox host name.")
        parser.add_argument("--inbox-port", type=int, help="Inbox server port.")
        parser.add_argument(
            "--inbox-protocol",
            choices=[EmailInbox.IMAP, EmailInbox.POP3],
            help="Inbox protocol.",
        )
        parser.add_argument("--inbox-password", help="Inbox password.")
        parser.add_argument("--inbox-priority", type=int, help="Inbox priority.")
        parser.add_argument(
            "--inbox-use-ssl",
            action="store_true",
            help="Enable SSL for inbox connection.",
        )
        parser.add_argument(
            "--inbox-no-ssl",
            action="store_true",
            help="Disable SSL for inbox connection.",
        )
        parser.add_argument(
            "--inbox-enabled",
            action="store_true",
            help="Mark inbox as enabled.",
        )
        parser.add_argument(
            "--inbox-disabled",
            action="store_true",
            help="Mark inbox as disabled.",
        )

        parser.add_argument("--outbox-host", help="Outbox SMTP host name.")
        parser.add_argument("--outbox-port", type=int, help="Outbox SMTP port.")
        parser.add_argument("--outbox-username", help="Outbox SMTP username.")
        parser.add_argument("--outbox-password", help="Outbox SMTP password.")
        parser.add_argument("--outbox-from-email", help="Default from address for outbox.")
        parser.add_argument("--outbox-priority", type=int, help="Outbox priority.")
        parser.add_argument("--outbox-use-tls", action="store_true", help="Enable SMTP TLS.")
        parser.add_argument("--outbox-no-tls", action="store_true", help="Disable SMTP TLS.")
        parser.add_argument("--outbox-use-ssl", action="store_true", help="Enable SMTP SSL.")
        parser.add_argument("--outbox-no-ssl", action="store_true", help="Disable SMTP SSL.")
        parser.add_argument("--outbox-enabled", action="store_true", help="Mark outbox as enabled.")
        parser.add_argument("--outbox-disabled", action="store_true", help="Mark outbox as disabled.")

        parser.add_argument("--bridge-name", help="Bridge name.")
        parser.add_argument("--bridge-inbox", type=int, help="Inbox id for bridge relation.")
        parser.add_argument("--bridge-outbox", type=int, help="Outbox id for bridge relation.")

        parser.add_argument("--send", action="store_true", help="Send an outbound email.")
        parser.add_argument("--to", help="Comma-separated list of recipients for --send.")
        parser.add_argument("--subject", default="", help="Email subject for --send or --search.")
        parser.add_argument("--message", default="", help="Email body message for --send.")
        parser.add_argument("--from-email", help="Override from address for --send.")

        parser.add_argument("--search", action="store_true", help="Search inbound messages.")
        parser.add_argument("--search-from", default="", help="From-address filter for --search.")
        parser.add_argument("--search-body", default="", help="Body filter for --search.")
        parser.add_argument("--search-limit", type=int, default=10, help="Maximum search results.")
        parser.add_argument("--regex", action="store_true", help="Use regex filters for --search.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute requested operations and print a report when no action flags are used."""

        configured = False

        if self._has_inbox_changes(options):
            inbox = self._configure_inbox(options)
            configured = True
            self.stdout.write(self.style.SUCCESS(f"Configured inbox #{inbox.pk}"))

        if self._has_outbox_changes(options):
            outbox = self._configure_outbox(options)
            configured = True
            self.stdout.write(self.style.SUCCESS(f"Configured outbox #{outbox.pk}"))

        if self._has_bridge_changes(options):
            bridge = self._configure_bridge(options)
            configured = True
            self.stdout.write(self.style.SUCCESS(f"Configured bridge #{bridge.pk}"))

        if options["send"]:
            self._send_email(options)
            configured = True

        if options["search"]:
            self._search_email(options)
            configured = True

        if not configured:
            self._report()

    def _resolve_user(self, user_id: int | None):
        """Resolve a user by id or return ``None`` when not provided."""

        if user_id is None:
            return None
        user_model = get_user_model()
        try:
            return user_model.objects.get(pk=user_id)
        except user_model.DoesNotExist as exc:
            raise CommandError(f"User not found: {user_id}") from exc

    def _boolean_option(self, options: dict[str, Any], true_key: str, false_key: str) -> bool | None:
        """Resolve a tri-state boolean from positive/negative CLI flags."""

        if options[true_key] and options[false_key]:
            raise CommandError(f"Cannot use --{true_key.replace('_', '-')} with --{false_key.replace('_', '-')}.")
        if options[true_key]:
            return True
        if options[false_key]:
            return False
        return None

    def _has_inbox_changes(self, options: dict[str, Any]) -> bool:
        """Return whether inbox configuration flags were supplied."""

        return any(
            (value := options.get(key)) is not None and not (isinstance(value, bool) and value is False)
            for key in self.INBOX_EDITABLE_KEYS
        )

    def _has_outbox_changes(self, options: dict[str, Any]) -> bool:
        """Return whether outbox configuration flags were supplied."""

        return any(
            (value := options.get(key)) is not None and not (isinstance(value, bool) and value is False)
            for key in self.OUTBOX_EDITABLE_KEYS
        )

    def _has_bridge_changes(self, options: dict[str, Any]) -> bool:
        """Return whether bridge configuration flags were supplied."""

        return any(options.get(key) is not None for key in self.BRIDGE_EDITABLE_KEYS)

    def _configure_inbox(self, options: dict[str, Any]) -> EmailInbox:
        """Create or update an inbox profile from command options."""

        inbox_id = options.get("inbox")
        if inbox_id is not None:
            try:
                inbox = EmailInbox.objects.get(pk=inbox_id)
            except EmailInbox.DoesNotExist as exc:
                raise CommandError(f"Inbox not found: {inbox_id}") from exc
        else:
            inbox = EmailInbox()

        user = self._resolve_user(options.get("owner_user"))
        if user is not None:
            inbox.user = user
            inbox.group = None

        mapping = {
            "username": options.get("inbox_username"),
            "host": options.get("inbox_host"),
            "port": options.get("inbox_port"),
            "protocol": options.get("inbox_protocol"),
            "password": options.get("inbox_password"),
            "priority": options.get("inbox_priority"),
        }
        for field, value in mapping.items():
            if value is not None:
                setattr(inbox, field, value)

        ssl_value = self._boolean_option(options, "inbox_use_ssl", "inbox_no_ssl")
        if ssl_value is not None:
            inbox.use_ssl = ssl_value

        enabled_value = self._boolean_option(options, "inbox_enabled", "inbox_disabled")
        if enabled_value is not None:
            inbox.is_enabled = enabled_value

        if inbox.pk is None and inbox.user_id is None and inbox.group_id is None and inbox.avatar_id is None:
            raise CommandError("Creating an inbox requires --owner-user.")

        inbox.full_clean()
        inbox.save()
        return inbox

    def _configure_outbox(self, options: dict[str, Any]) -> EmailOutbox:
        """Create or update an outbox profile from command options."""

        outbox_id = options.get("outbox")
        if outbox_id is not None:
            try:
                outbox = EmailOutbox.objects.get(pk=outbox_id)
            except EmailOutbox.DoesNotExist as exc:
                raise CommandError(f"Outbox not found: {outbox_id}") from exc
        else:
            outbox = EmailOutbox()

        user = self._resolve_user(options.get("owner_user"))
        if user is not None:
            outbox.user = user
            outbox.group = None

        mapping = {
            "host": options.get("outbox_host"),
            "port": options.get("outbox_port"),
            "username": options.get("outbox_username"),
            "password": options.get("outbox_password"),
            "from_email": options.get("outbox_from_email"),
            "priority": options.get("outbox_priority"),
        }
        for field, value in mapping.items():
            if value is not None:
                setattr(outbox, field, value)

        tls_value = self._boolean_option(options, "outbox_use_tls", "outbox_no_tls")
        if tls_value is not None:
            outbox.use_tls = tls_value

        ssl_value = self._boolean_option(options, "outbox_use_ssl", "outbox_no_ssl")
        if ssl_value is not None:
            outbox.use_ssl = ssl_value

        enabled_value = self._boolean_option(options, "outbox_enabled", "outbox_disabled")
        if enabled_value is not None:
            outbox.is_enabled = enabled_value

        if outbox.pk is None and outbox.user_id is None and outbox.group_id is None and outbox.avatar_id is None:
            raise CommandError("Creating an outbox requires --owner-user.")

        outbox.full_clean()
        outbox.save()
        return outbox

    def _configure_bridge(self, options: dict[str, Any]) -> EmailBridge:
        """Create or update a bridge profile from command options."""

        bridge_id = options.get("bridge")
        if bridge_id is not None:
            try:
                bridge = EmailBridge.objects.get(pk=bridge_id)
            except EmailBridge.DoesNotExist as exc:
                raise CommandError(f"Bridge not found: {bridge_id}") from exc
        else:
            bridge = EmailBridge()

        if options.get("bridge_name") is not None:
            bridge.name = options["bridge_name"]

        bridge_inbox = options.get("bridge_inbox")
        if bridge_inbox is not None:
            try:
                bridge.inbox = EmailInbox.objects.get(pk=bridge_inbox)
            except EmailInbox.DoesNotExist as exc:
                raise CommandError(f"Bridge inbox not found: {bridge_inbox}") from exc

        bridge_outbox = options.get("bridge_outbox")
        if bridge_outbox is not None:
            try:
                bridge.outbox = EmailOutbox.objects.get(pk=bridge_outbox)
            except EmailOutbox.DoesNotExist as exc:
                raise CommandError(f"Bridge outbox not found: {bridge_outbox}") from exc

        if bridge.pk is None and (not bridge.inbox_id or not bridge.outbox_id):
            raise CommandError("Creating a bridge requires both --bridge-inbox and --bridge-outbox.")

        bridge.full_clean()
        bridge.save()
        return bridge

    def _send_email(self, options: dict[str, Any]) -> None:
        """Send an email using either a selected outbox or outbox auto-selection."""

        raw_recipients = options.get("to") or ""
        recipients = [item.strip() for item in raw_recipients.split(",") if item.strip()]
        if not recipients:
            raise CommandError("--send requires --to with at least one recipient.")

        outbox = None
        outbox_id = options.get("outbox")
        if outbox_id is not None:
            try:
                outbox = EmailOutbox.objects.get(pk=outbox_id)
            except EmailOutbox.DoesNotExist as exc:
                raise CommandError(f"Outbox not found: {outbox_id}") from exc

        from_email = options.get("from_email")
        if from_email and outbox is not None:
            outbox.from_email = from_email

        mailer.send(
            options.get("subject") or "",
            options.get("message") or "",
            recipients,
            from_email=from_email,
            outbox=outbox,
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS(f"Sent email to {', '.join(recipients)}"))

    def _search_email(self, options: dict[str, Any]) -> None:
        """Search inbound messages and print results as JSON."""

        limit = options.get("search_limit")
        try:
            limit = int(limit) if limit is not None else 10
        except (TypeError, ValueError):
            limit = 10

        if limit <= 0:
            raise CommandError("--search-limit must be a positive integer.")

        inbox_id = options.get("inbox")
        if inbox_id is None:
            inbox = EmailInbox.objects.filter(is_enabled=True).order_by("-priority", "id").first()
            if inbox is None:
                raise CommandError("No enabled inbox is available for search.")
        else:
            try:
                inbox = EmailInbox.objects.get(pk=inbox_id)
            except EmailInbox.DoesNotExist as exc:
                raise CommandError(f"Inbox not found: {inbox_id}") from exc

        results = inbox.search_messages(
            subject=options.get("subject") or "",
            from_address=options.get("search_from") or "",
            body=options.get("search_body") or "",
            limit=limit,
            use_regular_expressions=bool(options.get("regex")),
        )
        self.stdout.write(json.dumps(results, indent=2, sort_keys=True, default=str))

    def _report(self) -> None:
        """Print inbox/outbox/bridge configuration details."""

        report = {
            "inboxes": [
                {
                    "id": inbox.pk,
                    "owner": inbox.owner_display(),
                    "username": inbox.username,
                    "host": inbox.host,
                    "port": inbox.port,
                    "protocol": inbox.protocol,
                    "use_ssl": inbox.use_ssl,
                    "is_enabled": inbox.is_enabled,
                    "priority": inbox.priority,
                }
                for inbox in EmailInbox.objects.select_related("user", "group", "avatar")
            ],
            "outboxes": [
                {
                    "id": outbox.pk,
                    "owner": outbox.owner_display(),
                    "username": outbox.username,
                    "host": outbox.host,
                    "port": outbox.port,
                    "from_email": outbox.from_email,
                    "use_tls": outbox.use_tls,
                    "use_ssl": outbox.use_ssl,
                    "is_enabled": outbox.is_enabled,
                    "priority": outbox.priority,
                }
                for outbox in EmailOutbox.objects.select_related("user", "group", "avatar", "node")
            ],
            "bridges": [
                {
                    "id": bridge.pk,
                    "name": bridge.name,
                    "inbox_id": bridge.inbox_id,
                    "outbox_id": bridge.outbox_id,
                }
                for bridge in EmailBridge.objects.select_related("inbox", "outbox")
            ],
        }
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True, default=str))
