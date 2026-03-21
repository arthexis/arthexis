"""Management command for reporting and operating email profiles."""

from __future__ import annotations

import json
from argparse import SUPPRESS
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.emails import mailer
from apps.emails.models import EmailBridge, EmailInbox, EmailOutbox


class Command(BaseCommand):
    """Report and manage email inbox/outbox/bridge profiles from the CLI."""

    INBOX_EDITABLE_KEYS = frozenset(
        {
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
    )
    OUTBOX_EDITABLE_KEYS = frozenset(
        {
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
    )
    BRIDGE_EDITABLE_KEYS = frozenset({"bridge_name", "bridge_inbox", "bridge_outbox"})

    help = (
        "Preferred interface: email <inbox|outbox|bridge|send|search|list> ...\n"
        "Configure inbox/outbox/bridge records, send outbound email, search inboxes, and report configuration."
    )

    def add_arguments(self, parser) -> None:
        """Register verb-based subcommands plus hidden compatibility flags."""

        parser.add_argument(
            "--owner-user",
            type=int,
            help=SUPPRESS,
        )
        self._add_legacy_arguments(parser)

        subparsers = parser.add_subparsers(dest="action")

        list_parser = subparsers.add_parser("list", aliases=["ls"], help="List inbox, outbox, and bridge configuration.")
        list_parser.set_defaults(action="list")

        inbox_parser = subparsers.add_parser("inbox", aliases=["ib"], help="Show or configure an inbox.")
        inbox_parser.set_defaults(action="inbox")
        inbox_parser.add_argument("inbox_id", nargs="?", type=int, help="Inbox id to show or update.")
        inbox_parser.add_argument("--owner-user", type=int, help="Owner user id for created or updated inboxes.")
        self._add_inbox_arguments(inbox_parser)

        outbox_parser = subparsers.add_parser("outbox", aliases=["ob"], help="Show or configure an outbox.")
        outbox_parser.set_defaults(action="outbox")
        outbox_parser.add_argument("outbox_id", nargs="?", type=int, help="Outbox id to show or update.")
        outbox_parser.add_argument("--owner-user", type=int, help="Owner user id for created or updated outboxes.")
        self._add_outbox_arguments(outbox_parser)

        bridge_parser = subparsers.add_parser("bridge", aliases=["br"], help="Show or configure an inbox/outbox bridge.")
        bridge_parser.set_defaults(action="bridge")
        bridge_parser.add_argument("bridge_id", nargs="?", type=int, help="Bridge id to show or update.")
        self._add_bridge_arguments(bridge_parser)

        send_parser = subparsers.add_parser("send", aliases=["tx"], help="Send an outbound email.")
        send_parser.set_defaults(action="send")
        send_parser.add_argument("outbox_id", nargs="?", type=int, help="Optional outbox id to use.")
        send_parser.add_argument("-t", "--to", help="Comma-separated recipients.")
        send_parser.add_argument("-s", "--subject", default="", help="Email subject.")
        send_parser.add_argument("-m", "--message", default="", help="Email body message.")
        send_parser.add_argument("-f", "--from-email", help="Override from address.")

        search_parser = subparsers.add_parser("search", aliases=["find"], help="Search inbound messages.")
        search_parser.set_defaults(action="search")
        search_parser.add_argument("inbox_id", nargs="?", type=int, help="Optional inbox id to search.")
        search_parser.add_argument("-s", "--subject", default="", help="Subject filter.")
        search_parser.add_argument("-f", "--from", dest="search_from", default="", help="From-address filter.")
        search_parser.add_argument("-b", "--body", dest="search_body", default="", help="Body filter.")
        search_parser.add_argument("-n", "--limit", dest="search_limit", type=int, default=10, help="Maximum results.")
        search_parser.add_argument("-r", "--regex", action="store_true", help="Use regular expressions for filters.")

    def _add_legacy_arguments(self, parser) -> None:
        """Register hidden flat flags for backward compatibility."""

        parser.add_argument("--inbox", type=int, help=SUPPRESS)
        parser.add_argument("--outbox", type=int, help=SUPPRESS)
        parser.add_argument("--bridge", type=int, help=SUPPRESS)
        self._add_inbox_arguments(parser, help_text=SUPPRESS, include_short_aliases=False)
        self._add_outbox_arguments(parser, help_text=SUPPRESS, include_short_aliases=False)
        self._add_bridge_arguments(parser, help_text=SUPPRESS, include_short_aliases=False)
        parser.add_argument("--send", action="store_true", help=SUPPRESS)
        parser.add_argument("--to", help=SUPPRESS)
        parser.add_argument("--subject", default="", help=SUPPRESS)
        parser.add_argument("--message", default="", help=SUPPRESS)
        parser.add_argument("--from-email", help=SUPPRESS)
        parser.add_argument("--search", action="store_true", help=SUPPRESS)
        parser.add_argument("--search-from", default="", help=SUPPRESS)
        parser.add_argument("--search-body", default="", help=SUPPRESS)
        parser.add_argument("--search-limit", type=int, default=10, help=SUPPRESS)
        parser.add_argument("--regex", action="store_true", help=SUPPRESS)

    def _add_inbox_arguments(
        self, parser, help_text: str | None = None, include_short_aliases: bool = True
    ) -> None:
        """Register inbox-specific configuration options on ``parser``."""

        username_flags = ["--inbox-username"]
        host_flags = ["--inbox-host"]
        port_flags = ["--inbox-port"]
        protocol_flags = ["--inbox-protocol"]
        password_flags = ["--inbox-password"]
        priority_flags = ["--inbox-priority"]
        ssl_flags = ["--inbox-use-ssl"]
        no_ssl_flags = ["--inbox-no-ssl"]
        enabled_flags = ["--inbox-enabled"]
        disabled_flags = ["--inbox-disabled"]
        if include_short_aliases:
            username_flags.insert(0, "--username")
            host_flags.insert(0, "--host")
            port_flags.insert(0, "--port")
            protocol_flags.insert(0, "--protocol")
            password_flags.insert(0, "--password")
            priority_flags.insert(0, "--priority")
            ssl_flags.insert(0, "--ssl")
            no_ssl_flags.insert(0, "--no-ssl")
            enabled_flags.insert(0, "--enabled")
            disabled_flags.insert(0, "--disabled")
        parser.add_argument(*username_flags, dest="inbox_username", help=help_text or "Inbox login username.")
        parser.add_argument(*host_flags, dest="inbox_host", help=help_text or "Inbox host name.")
        parser.add_argument(*port_flags, dest="inbox_port", type=int, help=help_text or "Inbox server port.")
        parser.add_argument(
            *protocol_flags,
            dest="inbox_protocol",
            choices=[EmailInbox.IMAP, EmailInbox.POP3],
            help=help_text or "Inbox protocol.",
        )
        parser.add_argument(*password_flags, dest="inbox_password", help=help_text or "Inbox password.")
        parser.add_argument(*priority_flags, dest="inbox_priority", type=int, help=help_text or "Inbox priority.")
        parser.add_argument(*ssl_flags, dest="inbox_use_ssl", action="store_true", help=help_text or "Enable SSL for inbox connection.")
        parser.add_argument(*no_ssl_flags, dest="inbox_no_ssl", action="store_true", help=help_text or "Disable SSL for inbox connection.")
        parser.add_argument(*enabled_flags, dest="inbox_enabled", action="store_true", help=help_text or "Mark inbox as enabled.")
        parser.add_argument(*disabled_flags, dest="inbox_disabled", action="store_true", help=help_text or "Mark inbox as disabled.")

    def _add_outbox_arguments(
        self, parser, help_text: str | None = None, include_short_aliases: bool = True
    ) -> None:
        """Register outbox-specific configuration options on ``parser``."""

        host_flags = ["--outbox-host"]
        port_flags = ["--outbox-port"]
        username_flags = ["--outbox-username"]
        password_flags = ["--outbox-password"]
        from_flags = ["--outbox-from-email"]
        if include_short_aliases:
            from_flags.insert(0, "--from-email")
        priority_flags = ["--outbox-priority"]
        tls_flags = ["--outbox-use-tls"]
        no_tls_flags = ["--outbox-no-tls"]
        ssl_flags = ["--outbox-use-ssl"]
        no_ssl_flags = ["--outbox-no-ssl"]
        enabled_flags = ["--outbox-enabled"]
        disabled_flags = ["--outbox-disabled"]
        if include_short_aliases:
            host_flags.insert(0, "--host")
            port_flags.insert(0, "--port")
            username_flags.insert(0, "--username")
            password_flags.insert(0, "--password")
            from_flags[:0] = ["--from", "--from-email"]
            priority_flags.insert(0, "--priority")
            tls_flags.insert(0, "--tls")
            no_tls_flags.insert(0, "--no-tls")
            ssl_flags.insert(0, "--ssl")
            no_ssl_flags.insert(0, "--no-ssl")
            enabled_flags.insert(0, "--enabled")
            disabled_flags.insert(0, "--disabled")
        parser.add_argument(*host_flags, dest="outbox_host", help=help_text or "Outbox SMTP host name.")
        parser.add_argument(*port_flags, dest="outbox_port", type=int, help=help_text or "Outbox SMTP port.")
        parser.add_argument(*username_flags, dest="outbox_username", help=help_text or "Outbox SMTP username.")
        parser.add_argument(*password_flags, dest="outbox_password", help=help_text or "Outbox SMTP password.")
        parser.add_argument(*from_flags, dest="outbox_from_email", help=help_text or "Default from address for the outbox.")
        parser.add_argument(*priority_flags, dest="outbox_priority", type=int, help=help_text or "Outbox priority.")
        parser.add_argument(*tls_flags, dest="outbox_use_tls", action="store_true", help=help_text or "Enable SMTP TLS.")
        parser.add_argument(*no_tls_flags, dest="outbox_no_tls", action="store_true", help=help_text or "Disable SMTP TLS.")
        parser.add_argument(*ssl_flags, dest="outbox_use_ssl", action="store_true", help=help_text or "Enable SMTP SSL.")
        parser.add_argument(*no_ssl_flags, dest="outbox_no_ssl", action="store_true", help=help_text or "Disable SMTP SSL.")
        parser.add_argument(*enabled_flags, dest="outbox_enabled", action="store_true", help=help_text or "Mark outbox as enabled.")
        parser.add_argument(*disabled_flags, dest="outbox_disabled", action="store_true", help=help_text or "Mark outbox as disabled.")

    def _add_bridge_arguments(
        self, parser, help_text: str | None = None, include_short_aliases: bool = True
    ) -> None:
        """Register bridge-specific configuration options on ``parser``."""

        name_flags = ["--bridge-name"]
        inbox_flags = ["--bridge-inbox"]
        outbox_flags = ["--bridge-outbox"]
        if include_short_aliases:
            name_flags.insert(0, "--name")
            inbox_flags.insert(0, "--inbox")
            outbox_flags.insert(0, "--outbox")
        parser.add_argument(*name_flags, dest="bridge_name", help=help_text or "Bridge name.")
        parser.add_argument(*inbox_flags, dest="bridge_inbox", type=int, help=help_text or "Inbox id for the bridge relation.")
        parser.add_argument(*outbox_flags, dest="bridge_outbox", type=int, help=help_text or "Outbox id for the bridge relation.")

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the requested verb-based command or the legacy flat flow."""

        normalized_options = self._normalize_options(options)
        action = normalized_options.get("action")

        if action == "list":
            self._report()
            return
        if action == "inbox":
            self._handle_inbox_action(normalized_options)
            return
        if action == "outbox":
            self._handle_outbox_action(normalized_options)
            return
        if action == "bridge":
            self._handle_bridge_action(normalized_options)
            return
        if action == "send":
            self._send_email(normalized_options)
            return
        if action == "search":
            self._search_email(normalized_options)
            return

        configured = False
        if self._has_inbox_changes(normalized_options):
            inbox = self._configure_inbox(normalized_options)
            configured = True
            self.stdout.write(self.style.SUCCESS(f"Configured inbox #{inbox.pk}"))
        if self._has_outbox_changes(normalized_options):
            outbox = self._configure_outbox(normalized_options)
            configured = True
            self.stdout.write(self.style.SUCCESS(f"Configured outbox #{outbox.pk}"))
        if self._has_bridge_changes(normalized_options):
            bridge = self._configure_bridge(normalized_options)
            configured = True
            self.stdout.write(self.style.SUCCESS(f"Configured bridge #{bridge.pk}"))
        if normalized_options.get("send"):
            self._send_email(normalized_options)
            configured = True
        if normalized_options.get("search"):
            self._search_email(normalized_options)
            configured = True
        if not configured:
            self._report()

    def _normalize_options(self, options: dict[str, Any]) -> dict[str, Any]:
        """Map subcommand positional values onto the legacy option names."""

        normalized = dict(options)
        action = normalized.get("action")
        if action == "inbox" and normalized.get("inbox_id") is not None:
            normalized["inbox"] = normalized["inbox_id"]
        if action == "outbox" and normalized.get("outbox_id") is not None:
            normalized["outbox"] = normalized["outbox_id"]
        if action == "bridge" and normalized.get("bridge_id") is not None:
            normalized["bridge"] = normalized["bridge_id"]
        if action == "send":
            normalized["send"] = True
            if normalized.get("outbox_id") is not None:
                normalized["outbox"] = normalized["outbox_id"]
        if action == "search":
            normalized["search"] = True
            if normalized.get("inbox_id") is not None:
                normalized["inbox"] = normalized["inbox_id"]
        return normalized

    def _handle_inbox_action(self, options: dict[str, Any]) -> None:
        """Show or configure a single inbox using the inbox subcommand."""

        if self._has_inbox_changes(options) or options.get("owner_user") is not None or options.get("inbox") is None:
            if self._has_inbox_changes(options) or options.get("owner_user") is not None:
                inbox = self._configure_inbox(options)
                self.stdout.write(self.style.SUCCESS(f"Configured inbox #{inbox.pk}"))
                return
        self._report_inboxes(options.get("inbox"))

    def _handle_outbox_action(self, options: dict[str, Any]) -> None:
        """Show or configure a single outbox using the outbox subcommand."""

        if self._has_outbox_changes(options) or options.get("owner_user") is not None:
            outbox = self._configure_outbox(options)
            self.stdout.write(self.style.SUCCESS(f"Configured outbox #{outbox.pk}"))
            return
        self._report_outboxes(options.get("outbox"))

    def _handle_bridge_action(self, options: dict[str, Any]) -> None:
        """Show or configure a bridge using the bridge subcommand."""

        if self._has_bridge_changes(options):
            bridge = self._configure_bridge(options)
            self.stdout.write(self.style.SUCCESS(f"Configured bridge #{bridge.pk}"))
            return
        self._report_bridges(options.get("bridge"))

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
            raise CommandError("Creating a bridge requires both --inbox and --outbox.")

        bridge.full_clean()
        bridge.save()
        return bridge

    def _send_email(self, options: dict[str, Any]) -> None:
        """Send an email using either a selected outbox or outbox auto-selection."""

        raw_recipients = options.get("to") or ""
        recipients = [item.strip() for item in raw_recipients.split(",") if item.strip()]
        if not recipients:
            raise CommandError("send requires --to with at least one recipient.")

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
            raise CommandError("--limit must be a positive integer.")

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

        try:
            results = inbox.search_messages(
                subject=options.get("subject") or "",
                from_address=options.get("search_from") or "",
                body=options.get("search_body") or "",
                limit=limit,
                use_regular_expressions=bool(options.get("regex")),
            )
        except ValidationError as exc:
            raise CommandError(f"Search failed: {exc}") from exc
        self.stdout.write(json.dumps(results, indent=2, sort_keys=True, default=str))

    def _report_inboxes(self, inbox_id: int | None = None) -> None:
        """Print inbox configuration details as JSON."""

        inboxes = EmailInbox.objects.select_related("user", "group", "avatar")
        if inbox_id is not None:
            inboxes = inboxes.filter(pk=inbox_id)
            if not inboxes.exists():
                raise CommandError(f"Inbox not found: {inbox_id}")
        payload = [
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
            for inbox in inboxes
        ]
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True, default=str))

    def _report_outboxes(self, outbox_id: int | None = None) -> None:
        """Print outbox configuration details as JSON."""

        outboxes = EmailOutbox.objects.select_related("user", "group", "avatar", "node")
        if outbox_id is not None:
            outboxes = outboxes.filter(pk=outbox_id)
            if not outboxes.exists():
                raise CommandError(f"Outbox not found: {outbox_id}")
        payload = [
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
            for outbox in outboxes
        ]
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True, default=str))

    def _report_bridges(self, bridge_id: int | None = None) -> None:
        """Print bridge configuration details as JSON."""

        bridges = EmailBridge.objects.select_related("inbox", "outbox")
        if bridge_id is not None:
            bridges = bridges.filter(pk=bridge_id)
            if not bridges.exists():
                raise CommandError(f"Bridge not found: {bridge_id}")
        payload = [
            {
                "id": bridge.pk,
                "name": bridge.name,
                "inbox_id": bridge.inbox_id,
                "outbox_id": bridge.outbox_id,
            }
            for bridge in bridges
        ]
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True, default=str))

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
