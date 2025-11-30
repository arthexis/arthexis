import json
import logging
from datetime import timedelta
from decimal import Decimal
from math import ceil
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import contextlib
import re
import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.core.mail import get_connection
from django.db import models
from django.db.models import F, Q
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _

from apps.core import mailer
from apps.core.entity import Entity, EntityAllManager, EntityManager
from apps.sigils.fields import SigilShortAutoField
from apps.core.models import (
    EmailArtifact,
    GoogleCalendarProfile as CoreGoogleCalendarProfile,
    InviteLead as CoreInviteLead,
    Profile as CoreProfile,
    ReleaseManager as CoreReleaseManager,
    SecurityGroup as CoreSecurityGroup,
    User as CoreUser,
)
from apps.crms.models import OdooProfile as CoreOdooProfile, Product as CoreProduct
from apps.awg.models import PowerLead as CorePowerLead
from django_otp.plugins.otp_totp.models import (
    TOTPDevice as CoreTOTPDevice,
)
from apps.nodes.models import Node


logger = logging.getLogger(__name__)


class SlackApiError(RuntimeError):
    """Raised when Slack reports an error during API calls."""


class InviteLead(CoreInviteLead):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreInviteLead._meta.verbose_name
        verbose_name_plural = CoreInviteLead._meta.verbose_name_plural


class PowerLead(CorePowerLead):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CorePowerLead._meta.verbose_name
        verbose_name_plural = CorePowerLead._meta.verbose_name_plural


class SecurityGroup(CoreSecurityGroup):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreSecurityGroup._meta.verbose_name
        verbose_name_plural = CoreSecurityGroup._meta.verbose_name_plural


_SOCIAL_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}\Z)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


social_domain_validator = RegexValidator(
    regex=_SOCIAL_DOMAIN_PATTERN,
    message=_("Enter a valid domain name such as example.com."),
    code="invalid",
)


social_did_validator = RegexValidator(
    regex=r"^(|did:[a-z0-9]+:[A-Za-z0-9.\-_:]+)$",
    message=_("Enter a valid DID such as did:plc:1234abcd."),
    code="invalid",
)


class SocialProfile(CoreProfile):
    """Store configuration required to link social accounts such as Bluesky."""

    class Network(models.TextChoices):
        BLUESKY = "bluesky", _("Bluesky")
        DISCORD = "discord", _("Discord")

    profile_fields = (
        "handle",
        "domain",
        "did",
        "application_id",
        "public_key",
        "guild_id",
        "bot_token",
        "default_channel_id",
    )

    network = models.CharField(
        max_length=32,
        choices=Network.choices,
        default=Network.BLUESKY,
        help_text=_(
            "Select the social network you want to connect. Bluesky and Discord are supported."
        ),
    )
    handle = models.CharField(
        max_length=253,
        blank=True,
        help_text=_(
            "Bluesky handle that should resolve to Arthexis. Use the verified domain (for example arthexis.com)."
        ),
        validators=[social_domain_validator],
    )
    domain = models.CharField(
        max_length=253,
        blank=True,
        help_text=_(
            "Domain that hosts the Bluesky verification. Publish a _atproto TXT record or a /.well-known/atproto-did file with the DID below."
        ),
        validators=[social_domain_validator],
    )
    did = models.CharField(
        max_length=255,
        blank=True,
        help_text=_(
            "Optional DID that Bluesky assigns once the domain is linked (for example did:plc:1234abcd)."
        ),
        validators=[social_did_validator],
    )
    application_id = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Discord application ID used to control the bot."),
    )
    public_key = models.CharField(
        max_length=128,
        blank=True,
        help_text=_("Discord public key used to verify interaction requests."),
    )
    guild_id = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Discord guild (server) identifier where the bot should operate."),
    )
    bot_token = SigilShortAutoField(
        max_length=255,
        blank=True,
        help_text=_("Discord bot token required for authenticated actions."),
    )
    default_channel_id = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Optional Discord channel identifier used for default messaging."),
    )

    def clean(self):
        super().clean()

        self.handle = (self.handle or "").strip().lower()
        self.domain = (self.domain or "").strip().lower()

        if self.network == self.Network.DISCORD:
            for field_name in (
                "application_id",
                "guild_id",
                "public_key",
                "bot_token",
                "default_channel_id",
            ):
                value = getattr(self, field_name, "")
                if isinstance(value, str):
                    trimmed = value.strip()
                    if trimmed != value:
                        setattr(self, field_name, trimmed)

            errors = {}
            for required in ("application_id", "guild_id", "bot_token"):
                if not getattr(self, required):
                    errors[required] = _("This field is required for Discord profiles.")
            if errors:
                raise ValidationError(errors)

        if self.network == self.Network.BLUESKY:
            errors = {}
            if not self.handle:
                errors["handle"] = _("Please provide the Bluesky handle to verify.")
            if not self.domain:
                errors["domain"] = _("Please provide the Bluesky domain to verify.")
            if errors:
                raise ValidationError(errors)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.network == self.Network.DISCORD:
            if self.guild_id:
                return f"{self.guild_id}@discord"
            return "discord"

        if self.network == self.Network.BLUESKY:
            handle = (self.resolve_sigils("handle") or self.handle or self.domain).strip()
            network = (self.resolve_sigils("network") or self.network or "").strip()
            if handle:
                return f"{handle}@{network or self.Network.BLUESKY}"
            if network:
                return network

        network = dict(self.Network.choices).get(self.network)
        if network:
            return network

        owner = self.owner_display()
        return owner or super().__str__()

    class Meta(CoreProfile.Meta):
        verbose_name = _("Social Identity")
        verbose_name_plural = _("Social Identities")
        db_table = "core_socialprofile"
        constraints = [
            models.UniqueConstraint(
                fields=["network", "handle"],
                condition=~Q(handle=""),
                name="socialprofile_network_handle",
            ),
            models.UniqueConstraint(
                fields=["network", "domain"],
                condition=~Q(domain=""),
                name="socialprofile_network_domain",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="socialprofile_requires_owner",
            ),
        ]


class EmailInbox(CoreProfile):
    """Credentials and configuration for connecting to an email mailbox."""

    IMAP = "imap"
    POP3 = "pop3"
    PROTOCOL_CHOICES = [
        (IMAP, "IMAP"),
        (POP3, "POP3"),
    ]

    profile_fields = (
        "username",
        "host",
        "port",
        "password",
        "protocol",
        "use_ssl",
        "is_enabled",
        "priority",
    )
    username = SigilShortAutoField(
        max_length=255,
        help_text="Login name for the mailbox",
    )
    host = SigilShortAutoField(
        max_length=255,
        help_text=(
            "Examples: Gmail IMAP 'imap.gmail.com', Gmail POP3 'pop.gmail.com',"
            " GoDaddy IMAP 'imap.secureserver.net', GoDaddy POP3 'pop.secureserver.net'"
        ),
    )
    port = models.PositiveIntegerField(
        default=993,
        help_text=(
            "Common ports: Gmail IMAP 993, Gmail POP3 995, "
            "GoDaddy IMAP 993, GoDaddy POP3 995"
        ),
    )
    password = SigilShortAutoField(max_length=255)
    protocol = SigilShortAutoField(
        max_length=5,
        choices=PROTOCOL_CHOICES,
        default=IMAP,
        help_text=(
            "IMAP keeps emails on the server for access across devices; "
            "POP3 downloads messages to a single device and may remove them from the server"
        ),
    )
    use_ssl = models.BooleanField(default=True)
    is_enabled = models.BooleanField(
        default=True,
        help_text="Disable to remove this inbox from automatic selection.",
    )
    priority = models.IntegerField(
        default=0,
        help_text="Higher values are selected first when multiple inboxes are available.",
    )

    class Meta:
        verbose_name = "Email Inbox"
        verbose_name_plural = "Email Inboxes"
        db_table = "core_emailinbox"
        ordering = ["-priority", "id"]

    def test_connection(self):
        """Attempt to connect to the configured mailbox."""
        try:
            if self.protocol == self.IMAP:
                import imaplib

                conn = (
                    imaplib.IMAP4_SSL(self.host, self.port)
                    if self.use_ssl
                    else imaplib.IMAP4(self.host, self.port)
                )
                conn.login(self.username, self.password)
                conn.logout()
            else:
                import poplib

                conn = (
                    poplib.POP3_SSL(self.host, self.port)
                    if self.use_ssl
                    else poplib.POP3(self.host, self.port)
                )
                conn.user(self.username)
                conn.pass_(self.password)
                conn.quit()
            return True
        except Exception as exc:
            raise ValidationError(str(exc))

    def is_ready(self) -> bool:
        try:
            self.test_connection()
            return True
        except Exception:
            logger.warning(
                "EmailInbox %s failed readiness check", self.pk, exc_info=True
            )
            return False

    def search_messages(
        self,
        subject="",
        from_address="",
        body="",
        limit: int = 10,
        use_regular_expressions: bool = False,
    ):
        """Retrieve up to ``limit`` recent messages matching the filters."""

        def _compile(pattern: str | None):
            if not pattern:
                return None
            import re

            try:
                return re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValidationError(str(exc))

        subject_regex = sender_regex = body_regex = None
        if use_regular_expressions:
            subject_regex = _compile(subject)
            sender_regex = _compile(from_address)
            body_regex = _compile(body)

        def _matches(value: str, needle: str, regex):
            value = value or ""
            if regex is not None:
                return bool(regex.search(value))
            if not needle:
                return True
            return needle.lower() in value.lower()

        from email.header import decode_header

        def _get_body(msg):
            if msg.is_multipart():
                for part in msg.walk():
                    if (
                        part.get_content_type() == "text/plain"
                        and not part.get_filename()
                    ):
                        charset = part.get_content_charset() or "utf-8"
                        return part.get_payload(decode=True).decode(
                            charset, errors="ignore"
                        )
                return ""
            charset = msg.get_content_charset() or "utf-8"
            return msg.get_payload(decode=True).decode(charset, errors="ignore")

        def _decode_header_value(value):
            if not value:
                return ""
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="ignore")
            try:
                parts = decode_header(value)
            except Exception:
                return value if isinstance(value, str) else ""
            decoded = []
            for text, encoding in parts:
                if isinstance(text, bytes):
                    encodings_to_try = []
                    if encoding:
                        encodings_to_try.append(encoding)
                    encodings_to_try.extend(["utf-8", "latin-1"])
                    for candidate in encodings_to_try:
                        try:
                            decoded.append(
                                text.decode(candidate, errors="ignore")
                            )
                            break
                        except LookupError:
                            continue
                    else:
                        try:
                            decoded.append(text.decode("utf-8", errors="ignore"))
                        except Exception:
                            decoded.append("")
                else:
                    decoded.append(text)
            return "".join(decoded)

        if self.protocol == self.IMAP:
            import imaplib
            import email

            def _decode_imap_bytes(value):
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="ignore")
                return str(value)

            conn = (
                imaplib.IMAP4_SSL(self.host, self.port)
                if self.use_ssl
                else imaplib.IMAP4(self.host, self.port)
            )
            try:
                conn.login(self.username, self.password)
                typ, data = conn.select("INBOX")
                if typ != "OK":
                    message = " ".join(_decode_imap_bytes(item) for item in data or [])
                    if not message:
                        message = "Unable to select INBOX"
                    raise ValidationError(message)

                fetch_limit = (
                    limit if not use_regular_expressions else max(limit * 5, limit)
                )
                if use_regular_expressions:
                    typ, data = conn.search(None, "ALL")
                else:
                    criteria = []
                    charset = None

                    def _quote_bytes(raw: bytes) -> bytes:
                        return b'"' + raw.replace(b"\\", b"\\\\").replace(b'"', b'\\"') + b'"'

                    def _append(term: str, value: str):
                        nonlocal charset
                        if not value:
                            return
                        try:
                            value.encode("ascii")
                            encoded_value = value
                        except UnicodeEncodeError:
                            charset = charset or "UTF-8"
                            encoded_value = _quote_bytes(value.encode("utf-8"))
                        else:
                            if any(ch.isspace() for ch in value):
                                encoded_value = value.replace("\\", "\\\\").replace(
                                    '"', '\\"'
                                )
                                encoded_value = f'"{encoded_value}"'
                        criteria.extend([term, encoded_value])

                    _append("SUBJECT", subject)
                    _append("FROM", from_address)
                    _append("TEXT", body)

                    if not criteria:
                        typ, data = conn.search(None, "ALL")
                    else:
                        typ, data = conn.search(charset, *criteria)

                if typ != "OK":
                    message = " ".join(_decode_imap_bytes(item) for item in data or [])
                    if not message:
                        message = "Unable to search mailbox"
                    raise ValidationError(message)

                ids = data[0].split()[-fetch_limit:]
                messages = []
                for mid in ids:
                    typ, msg_data = conn.fetch(mid, "(RFC822)")
                    if typ != "OK" or not msg_data:
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    body_text = _get_body(msg)
                    subj_value = _decode_header_value(msg.get("Subject", ""))
                    from_value = _decode_header_value(msg.get("From", ""))
                    if not (
                        _matches(subj_value, subject, subject_regex)
                        and _matches(from_value, from_address, sender_regex)
                        and _matches(body_text, body, body_regex)
                    ):
                        continue
                    messages.append(
                        {
                            "subject": subj_value,
                            "from": from_value,
                            "body": body_text,
                            "date": msg.get("Date", ""),
                        }
                    )
                    if len(messages) >= limit:
                        break
                return list(reversed(messages))
            finally:
                try:
                    conn.logout()
                except Exception:  # pragma: no cover - best effort cleanup
                    pass

        import poplib
        import email

        conn = (
            poplib.POP3_SSL(self.host, self.port)
            if self.use_ssl
            else poplib.POP3(self.host, self.port)
        )
        conn.user(self.username)
        conn.pass_(self.password)
        count = len(conn.list()[1])
        messages = []
        for i in range(count, 0, -1):
            resp, lines, octets = conn.retr(i)
            msg = email.message_from_bytes(b"\n".join(lines))
            subj = _decode_header_value(msg.get("Subject", ""))
            frm = _decode_header_value(msg.get("From", ""))
            body_text = _get_body(msg)
            if not (
                _matches(subj, subject, subject_regex)
                and _matches(frm, from_address, sender_regex)
                and _matches(body_text, body, body_regex)
            ):
                continue
            messages.append(
                {
                    "subject": subj,
                    "from": frm,
                    "body": body_text,
                    "date": msg.get("Date", ""),
                }
            )
            if len(messages) >= limit:
                break
        conn.quit()
        return messages

    def __str__(self) -> str:
        username = (self.username or "").strip()
        if username:
            return username
        return super().__str__()



class EmailCollector(Entity):
    """Search an inbox for matching messages and extract data via sigils."""

    name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional label to identify this collector.",
    )
    inbox = models.ForeignKey(
        "teams.EmailInbox",
        related_name="collectors",
        on_delete=models.CASCADE,
    )
    subject = models.CharField(max_length=255, blank=True)
    sender = models.CharField(max_length=255, blank=True)
    body = models.CharField(max_length=255, blank=True)
    fragment = models.CharField(
        max_length=255,
        blank=True,
        help_text="Pattern with [sigils] to extract values from the body.",
    )
    use_regular_expressions = models.BooleanField(
        default=False,
        help_text="Treat subject, sender and body filters as regular expressions (case-insensitive).",
    )

    def _parse_sigils(self, text: str) -> dict[str, str]:
        """Extract values from ``text`` according to ``fragment`` sigils."""
        if not self.fragment:
            return {}
        import re

        parts = re.split(r"\[([^\]]+)\]", self.fragment)
        pattern = ""
        for idx, part in enumerate(parts):
            if idx % 2 == 0:
                pattern += re.escape(part)
            else:
                pattern += f"(?P<{part}>.+)"
        match = re.search(pattern, text)
        if not match:
            return {}
        return {k: v.strip() for k, v in match.groupdict().items()}

    def __str__(self):  # pragma: no cover - simple representation
        if self.name:
            return self.name
        parts = []
        if self.subject:
            parts.append(self.subject)
        if self.sender:
            parts.append(self.sender)
        if not parts:
            parts.append(str(self.inbox))
        return " – ".join(parts)

    def search_messages(self, limit: int = 10):
        return self.inbox.search_messages(
            subject=self.subject,
            from_address=self.sender,
            body=self.body,
            limit=limit,
            use_regular_expressions=self.use_regular_expressions,
        )

    def collect(self, limit: int = 10) -> None:
        """Poll the inbox and store new artifacts until an existing one is found."""
        messages = self.search_messages(limit=limit)
        for msg in messages:
            fp = EmailArtifact.fingerprint_for(
                msg.get("subject", ""), msg.get("from", ""), msg.get("body", "")
            )
            if EmailArtifact.objects.filter(collector=self, fingerprint=fp).exists():
                break
            EmailArtifact.objects.create(
                collector=self,
                subject=msg.get("subject", ""),
                sender=msg.get("from", ""),
                body=msg.get("body", ""),
                sigils=self._parse_sigils(msg.get("body", "")),
                fingerprint=fp,
            )

    class Meta:
        verbose_name = _("Email Collector")
        verbose_name_plural = _("Email Collectors")
        db_table = "core_emailcollector"


class ReleaseManager(CoreReleaseManager):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreReleaseManager._meta.verbose_name
        verbose_name_plural = CoreReleaseManager._meta.verbose_name_plural


class EmailOutbox(CoreProfile):
    """SMTP credentials for sending mail."""

    profile_fields = (
        "host",
        "port",
        "username",
        "password",
        "use_tls",
        "use_ssl",
        "from_email",
        "priority",
    )

    node = models.OneToOneField(
        Node,
        on_delete=models.CASCADE,
        related_name="email_outbox",
        null=True,
        blank=True,
    )
    host = SigilShortAutoField(
        max_length=100,
        help_text=("Gmail: smtp.gmail.com. " "GoDaddy: smtpout.secureserver.net"),
    )
    port = models.PositiveIntegerField(
        default=587,
        help_text=("Gmail: 587 (TLS). " "GoDaddy: 587 (TLS) or 465 (SSL)"),
    )
    username = SigilShortAutoField(
        max_length=100,
        blank=True,
        help_text="Full email address for Gmail or GoDaddy",
    )
    password = SigilShortAutoField(
        max_length=100,
        blank=True,
        help_text="Email account password or app password",
    )
    use_tls = models.BooleanField(
        default=True,
        help_text="Check for Gmail or GoDaddy on port 587",
    )
    use_ssl = models.BooleanField(
        default=False,
        help_text="Check for GoDaddy on port 465; Gmail does not use SSL",
    )
    from_email = SigilShortAutoField(
        blank=True,
        verbose_name="From Email",
        max_length=254,
        help_text="Default From address; usually the same as username",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Disable to remove this outbox from automatic selection.",
    )
    priority = models.IntegerField(
        default=0,
        help_text="Higher values are selected first when multiple outboxes are available.",
    )

    class Meta:
        verbose_name = "Email Outbox"
        verbose_name_plural = "Email Outboxes"
        db_table = "nodes_emailoutbox"
        ordering = ["-priority", "id"]

    def __str__(self) -> str:
        username = (self.username or "").strip()
        if username:
            return username
        return super().__str__()

    def clean(self):
        if self.user_id or self.group_id:
            super().clean()
        else:
            super(CoreProfile, self).clean()

    def get_connection(self):
        backend_path = getattr(
            settings, "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
        )
        return get_connection(
            backend_path,
            host=self.host,
            port=self.port,
            username=self.username or None,
            password=self.password or None,
            use_tls=self.use_tls,
            use_ssl=self.use_ssl,
        )

    def send_mail(self, subject, message, recipient_list, from_email=None, **kwargs):
        from_email = from_email or self.from_email or settings.DEFAULT_FROM_EMAIL
        logger.info("EmailOutbox %s queueing email to %s", self.pk, recipient_list)
        return mailer.send(
            subject,
            message,
            recipient_list,
            from_email,
            outbox=self,
            **kwargs,
        )

    def owner_display(self):
        owner = super().owner_display()
        if owner:
            return owner
        return str(self.node) if self.node_id else ""

    def is_ready(self) -> bool:
        try:
            connection = self.get_connection()
            connection.open()
            connection.close()
            return True
        except Exception:
            logger.warning(
                "EmailOutbox %s failed readiness check", self.pk, exc_info=True
            )
            return False


def ensure_admin_email_mailboxes():
    """Create default inbox/outbox entries from Django email settings."""

    from django.contrib.auth import get_user_model
    from django.db import DatabaseError

    admin_email = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
    if not admin_email:
        return None, None

    try:
        UserModel = get_user_model()
        owner = (
            UserModel.objects.filter(is_superuser=True)
            .order_by("pk")
            .first()
        )
    except Exception:  # pragma: no cover - database not ready
        owner = None

    try:  # pragma: no cover - optional dependency during setup
        from apps.nodes.models import Node

        local_node = Node.get_local()
    except Exception:
        local_node = None

    outbox_defaults = {
        "host": getattr(settings, "EMAIL_HOST", ""),
        "port": getattr(settings, "EMAIL_PORT", 587),
        "username": getattr(settings, "EMAIL_HOST_USER", admin_email),
        "password": getattr(settings, "EMAIL_HOST_PASSWORD", ""),
        "use_tls": getattr(settings, "EMAIL_USE_TLS", False),
        "use_ssl": getattr(settings, "EMAIL_USE_SSL", False),
        "from_email": admin_email,
        "is_enabled": True,
        "priority": 100,
    }

    outbox_filters = {"from_email": admin_email}
    if local_node:
        outbox_filters = {"node": local_node}
    elif owner:
        outbox_filters = {"user": owner}

    inbox_defaults = {
        "host": getattr(settings, "EMAIL_HOST", ""),
        "port": 993,
        "username": admin_email,
        "password": getattr(settings, "EMAIL_HOST_PASSWORD", ""),
        "protocol": EmailInbox.IMAP,
        "use_ssl": True,
        "priority": 100,
    }

    try:
        outbox, _ = EmailOutbox.objects.update_or_create(
            defaults=outbox_defaults, **outbox_filters
        )
    except DatabaseError:  # pragma: no cover - tables may not exist yet
        outbox = None

    inbox = None
    if owner:
        try:
            inbox, _ = EmailInbox.objects.update_or_create(
                defaults=inbox_defaults,
                user=owner,
                username=admin_email,
            )
        except DatabaseError:  # pragma: no cover - tables may not exist yet
            inbox = None

    return inbox, outbox


class OdooProfile(CoreOdooProfile):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreOdooProfile._meta.verbose_name
        verbose_name_plural = CoreOdooProfile._meta.verbose_name_plural


class TOTPDevice(CoreTOTPDevice):
    supports_user_datum = True
    supports_seed_datum = True

    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = _("TOTP Device")
        verbose_name_plural = _("TOTP Devices")


class GoogleCalendarProfile(CoreGoogleCalendarProfile):
    class Meta:
        proxy = True
        app_label = "django_celery_beat"
        verbose_name = CoreGoogleCalendarProfile._meta.verbose_name
        verbose_name_plural = CoreGoogleCalendarProfile._meta.verbose_name_plural


class SlackBotProfile(CoreProfile):
    """Store credentials required to operate a Slack chatbot."""

    API_BASE_URL = "https://slack.com/api"

    profile_fields = (
        "node",
        "team_id",
        "bot_user_id",
        "bot_token",
        "signing_secret",
        "default_channels",
        "is_enabled",
    )

    node = models.OneToOneField(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="slack_bot",
        null=True,
        blank=True,
        help_text=_("Node that owns this Slack chatbot."),
    )
    team_id = models.CharField(
        max_length=32,
        help_text=_("Slack workspace team identifier (starts with T)."),
    )
    bot_user_id = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Slack bot user identifier (starts with U or B)."),
    )
    bot_token = SigilShortAutoField(
        max_length=255,
        help_text=_("Slack bot token used for authenticated API calls."),
    )
    signing_secret = SigilShortAutoField(
        max_length=255,
        help_text=_("Slack signing secret used to verify incoming requests."),
    )
    default_channels = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "Channel identifiers where Net Messages should be posted. Provide"
            " a JSON array of channel IDs (for example [\"C01ABCDE\"])."
        ),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable to stop the bot from posting to Slack."),
    )

    class Meta:
        verbose_name = _("Slack Chatbot")
        verbose_name_plural = _("Slack Chatbots")
        constraints = [
            models.UniqueConstraint(
                fields=["team_id"],
                name="slackbotprofile_team_id_unique",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                    | (Q(user__isnull=True) & Q(group__isnull=True) & Q(node__isnull=False))
                ),
                name="slackbotprofile_requires_owner",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        identifier = (self.resolve_sigils("team_id") or self.team_id or "").strip()
        owner = self.owner_display()
        if identifier and owner:
            return f"{identifier} ({owner})"
        if identifier:
            return identifier
        return owner or super().__str__()

    def clean(self):
        if self.user_id or self.group_id:
            super().clean()
        else:
            super(CoreProfile, self).clean()

        errors = {}

        if not self.node_id and not self.user_id and not self.group_id:
            errors["node"] = _("Assign the Slack bot to a node or owner.")

        team_id = (self.team_id or "").strip().upper()
        if not team_id:
            errors["team_id"] = _("Provide the Slack workspace team identifier.")
        elif not team_id.startswith("T"):
            errors["team_id"] = _("Slack team identifiers start with the letter T.")

        if not (self.resolve_sigils("bot_token") or (self.bot_token or "").strip()):
            errors["bot_token"] = _(
                "Provide the Slack bot token so Arthexis can send messages."
            )

        if not (self.resolve_sigils("signing_secret") or (self.signing_secret or "").strip()):
            errors["signing_secret"] = _(
                "Provide the Slack signing secret so incoming requests can be verified."
            )

        channels_error = self._normalize_channels()
        if channels_error:
            errors["default_channels"] = channels_error

        if errors:
            raise ValidationError(errors)

        self.team_id = team_id

    def save(self, *args, **kwargs):
        if self.team_id:
            self.team_id = self.team_id.strip().upper()
        if self.bot_user_id:
            self.bot_user_id = self.bot_user_id.strip().upper()
        self._normalize_channels()
        super().save(*args, **kwargs)

    def owner_display(self):  # pragma: no cover - simple representation helper
        owner = super().owner_display()
        if owner:
            return owner
        if self.node_id and self.node:
            return str(self.node)
        return ""

    def _normalize_channels(self) -> str | None:
        """Ensure ``default_channels`` stores a list of strings."""

        channels = self.default_channels or []
        if isinstance(channels, str):
            try:
                channels = json.loads(channels)
            except json.JSONDecodeError:
                return _("Channel IDs must be provided as a JSON array of strings.")
        if channels is None:
            channels = []
        if not isinstance(channels, list):
            return _("Channel IDs must be provided as a list of strings.")
        normalized: list[str] = []
        for value in channels:
            if value is None:
                continue
            if not isinstance(value, str):
                return _("Channel IDs must be provided as a list of strings.")
            cleaned = value.strip()
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        self.default_channels = normalized
        return None

    # Public helpers -------------------------------------------------

    def get_bot_token(self) -> str:
        return (self.resolve_sigils("bot_token") or self.bot_token or "").strip()

    def get_signing_secret(self) -> str:
        return (self.resolve_sigils("signing_secret") or self.signing_secret or "").strip()

    def get_channels(self) -> list[str]:
        return list(self.default_channels or [])

    def connect(self) -> dict[str, object]:
        """Validate the stored credentials by calling Slack's ``auth.test``."""

        data = self._api_post("auth.test", {})
        team_id = (data.get("team_id") or "").strip().upper()
        bot_user_id = (data.get("user_id") or "").strip().upper()
        updated_fields: list[str] = []
        if team_id and not self.team_id:
            self.team_id = team_id
            updated_fields.append("team_id")
        if bot_user_id and not self.bot_user_id:
            self.bot_user_id = bot_user_id
            updated_fields.append("bot_user_id")
        if updated_fields:
            self.save(update_fields=updated_fields)
        return data

    def broadcast_net_message(self, message: "NetMessage") -> None:
        """Post ``message`` to each configured Slack channel."""

        if not self.is_enabled:
            return
        token = self.get_bot_token()
        if not token:
            logger.debug("Slack bot %s skipped broadcast: missing token", self.pk)
            return
        channels = self.get_channels()
        if not channels:
            logger.debug(
                "Slack bot %s skipped broadcast: no default channels configured",
                self.pk,
            )
            return

        subject = (message.subject or "").strip()
        body = (message.body or "").strip()
        if subject and body:
            text = f"*{subject}*\n{body}"
        else:
            text = subject or body

        attachments = []
        for attachment in message.attachments or []:
            if not isinstance(attachment, dict):
                continue
            descriptor = attachment.get("description")
            if isinstance(descriptor, str) and descriptor.strip():
                attachments.append(descriptor.strip())
                continue
            try:
                attachments.append(json.dumps(attachment, ensure_ascii=False))
            except (TypeError, ValueError):
                continue

        for channel in channels:
            payload: dict[str, object] = {"channel": channel, "text": text or ""}
            if attachments:
                attachment_text = "\n".join(attachments)
                payload["text"] = (payload["text"] or "").strip()
                if payload["text"]:
                    payload["text"] += "\n" + attachment_text
                else:
                    payload["text"] = attachment_text
            try:
                self._api_post("chat.postMessage", payload)
            except SlackApiError:
                logger.exception(
                    "Slack bot %s failed to post NetMessage %s to channel %s",
                    self.pk,
                    getattr(message, "pk", None),
                    channel,
                )

    # Internal utilities --------------------------------------------

    def _api_post(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        token = self.get_bot_token()
        if not token:
            raise SlackApiError("missing_token")
        url = f"{self.API_BASE_URL}/{method}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        response = None
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=5)
        except requests.RequestException as exc:  # pragma: no cover - network issues
            raise SlackApiError("request_failed") from exc
        try:
            data: dict[str, object] | None
            try:
                data = response.json()
            except ValueError:
                data = None
            if not response.ok or not isinstance(data, dict) or not data.get("ok"):
                error = "unknown_error"
                if isinstance(data, dict):
                    error = str(data.get("error") or error)
                raise SlackApiError(error)
            return data
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()

    def _profile_fields(self) -> Iterable[str]:  # pragma: no cover - admin helper
        return self.profile_fields


class TaskCategoryManager(EntityManager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class TaskCategory(Entity):
    """Standardized categories for manual work assignments."""

    AVAILABILITY_NONE = "none"
    AVAILABILITY_IMMEDIATE = "immediate"
    AVAILABILITY_2_3_BUSINESS_DAYS = "2_3_business_days"
    AVAILABILITY_2_3_WEEKS = "2_3_weeks"
    AVAILABILITY_UNAVAILABLE = "unavailable"

    AVAILABILITY_CHOICES = [
        (AVAILABILITY_NONE, _("None")),
        (AVAILABILITY_IMMEDIATE, _("Immediate")),
        (AVAILABILITY_2_3_BUSINESS_DAYS, _("2-3 business days")),
        (AVAILABILITY_2_3_WEEKS, _("2-3 weeks")),
        (AVAILABILITY_UNAVAILABLE, _("Unavailable")),
    ]

    name = models.CharField(_("Name"), max_length=200)
    description = models.TextField(
        _("Description"),
        blank=True,
        help_text=_("Optional details supporting Markdown formatting."),
    )
    image = models.ImageField(
        _("Image"), upload_to="workgroup/task_categories/", blank=True
    )
    cost = models.DecimalField(
        _("Cost"),
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=_("Estimated fulfillment cost in local currency."),
    )
    default_duration = models.DurationField(
        _("Default duration"),
        null=True,
        blank=True,
        help_text=_("Typical time expected to complete tasks in this category."),
    )
    manager = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_task_categories",
        verbose_name=_("Manager"),
        help_text=_("User responsible for overseeing this category."),
    )
    odoo_products = models.ManyToManyField(
        CoreProduct,
        related_name="task_categories",
        verbose_name=_("Odoo products"),
        blank=True,
        help_text=_("Relevant Odoo products for this category."),
    )
    availability = models.CharField(
        _("Availability"),
        max_length=32,
        choices=AVAILABILITY_CHOICES,
        default=AVAILABILITY_NONE,
        help_text=_("Typical lead time for scheduling this work."),
    )
    requestor_group = models.ForeignKey(
        "core.SecurityGroup",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="task_categories_as_requestor",
        verbose_name=_("Requestor group"),
        help_text=_("Security group authorized to request this work."),
    )
    assigned_group = models.ForeignKey(
        "core.SecurityGroup",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="task_categories_as_assignee",
        verbose_name=_("Assigned to group"),
        help_text=_("Security group typically assigned to this work."),
    )

    objects = TaskCategoryManager()
    all_objects = EntityAllManager()

    class Meta:
        verbose_name = _("Task Category")
        verbose_name_plural = _("Task Categories")
        ordering = ("name",)
        db_table = "core_taskcategory"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

    natural_key.dependencies = []  # type: ignore[attr-defined]

    def availability_label(self) -> str:  # pragma: no cover - admin helper
        return self.get_availability_display()

    availability_label.short_description = _("Availability")  # type: ignore[attr-defined]


class ManualTask(Entity):
    """Manual work scheduled for nodes or locations."""

    description = models.TextField(
        _("Requestor Comments"),
        help_text=_("Detailed summary of the work to perform."),
    )
    category = models.ForeignKey(
        "teams.TaskCategory",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="manual_tasks",
        verbose_name=_("Category"),
        help_text=_("Select the standardized category for this work."),
    )
    assigned_user = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_manual_tasks",
        verbose_name=_("Assigned user"),
        help_text=_("Optional user responsible for the task."),
    )
    assigned_group = models.ForeignKey(
        "core.SecurityGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_manual_tasks",
        verbose_name=_("Potential assignees"),
        help_text=_("Security group containing users who can fulfill the task."),
    )
    manager = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_manual_tasks",
        verbose_name=_("Manager"),
        help_text=_("User overseeing the task."),
    )
    odoo_products = models.ManyToManyField(
        CoreProduct,
        blank=True,
        related_name="manual_tasks",
        verbose_name=_("Odoo products"),
        help_text=_("Products associated with the requested work."),
    )
    duration = models.DurationField(
        _("Expected duration"),
        null=True,
        blank=True,
        help_text=_("Estimated time to complete the task."),
    )
    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="manual_tasks",
        verbose_name=_("Node"),
        help_text=_("Node where this manual task should be completed."),
    )
    location = models.ForeignKey(
        "energy.Location",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="manual_tasks",
        verbose_name=_("Location"),
        help_text=_("Location associated with this manual task."),
    )
    scheduled_start = models.DateTimeField(
        _("Scheduled start"), help_text=_("Planned start time for this work."),
    )
    scheduled_end = models.DateTimeField(
        _("Scheduled end"), help_text=_("Planned completion time for this work."),
    )
    enable_notifications = models.BooleanField(
        _("Enable notifications"),
        default=False,
        help_text=_(
            "Send reminder emails to the assigned contacts when Celery notifications are available."
        ),
    )

    class Meta:
        verbose_name = _("Manual Task")
        verbose_name_plural = _("Manual Tasks")
        ordering = ("scheduled_start", "category__name")
        db_table = "core_manualtask"
        constraints = [
            models.CheckConstraint(
                name="manualtask_requires_target",
                condition=Q(node__isnull=False) | Q(location__isnull=False),
            ),
            models.CheckConstraint(
                name="manualtask_schedule_order",
                condition=Q(scheduled_end__gte=F("scheduled_start")),
            ),
        ]

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        if not self.node and not self.location:
            message = _("Select at least one node or location.")
            errors["node"] = [message]
            errors["location"] = [message]
        if self.scheduled_start and self.scheduled_end:
            if self.scheduled_end < self.scheduled_start:
                errors.setdefault("scheduled_end", []).append(
                    _("Scheduled end must be on or after the scheduled start."),
                )
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.category:
            return self.category.name
        if self.description:
            return self.description[:50]
        return super().__str__()

    # Notification helpers -------------------------------------------

    @staticmethod
    def _celery_lock_path() -> Path:
        return Path(settings.BASE_DIR) / ".locks" / "celery.lck"

    @classmethod
    def _is_celery_enabled(cls) -> bool:
        return cls._celery_lock_path().exists()

    def _iter_group_emails(self, group: CoreSecurityGroup | None) -> Iterator[str]:
        if not group or not group.pk:
            return
        queryset = group.user_set.filter(is_active=True).exclude(email="")
        for email in queryset.values_list("email", flat=True):
            normalized = (email or "").strip()
            if normalized:
                yield normalized

    def _iter_node_admin_emails(self) -> Iterator[str]:
        node = self.node
        if not node:
            return
        outbox = getattr(node, "email_outbox", None)
        if not outbox:
            return
        owner = outbox.owner
        if owner is None:
            return
        if hasattr(owner, "email"):
            email = (getattr(owner, "email", "") or "").strip()
            if email:
                yield email
            return
        yield from self._iter_group_emails(owner)

    def _iter_notification_recipients(self) -> Iterator[str]:
        seen: set[str] = set()

        if self.assigned_user_id and self.assigned_user:
            email = (self.assigned_user.email or "").strip()
            if email and email.lower() not in seen:
                seen.add(email.lower())
                yield email

        for email in self._iter_group_emails(self.assigned_group):
            normalized = email.lower()
            if normalized not in seen:
                seen.add(normalized)
                yield email

        for email in self._iter_node_admin_emails():
            normalized = email.lower()
            if normalized not in seen:
                seen.add(normalized)
                yield email

    def resolve_notification_recipients(self) -> list[str]:
        return list(self._iter_notification_recipients())

    def _format_datetime(self, value) -> str:
        if not value:
            return ""
        try:
            localized = timezone.localtime(value)
        except Exception:
            localized = value
        return formats.date_format(localized, "DATETIME_FORMAT")

    def _notification_subject(self, trigger: str) -> str:
        if trigger == "immediate":
            template = _("Manual task assigned: %(title)s")
        elif trigger == "24h":
            template = _("Manual task starts in 24 hours: %(title)s")
        elif trigger == "3h":
            template = _("Manual task starts in 3 hours: %(title)s")
        else:
            template = _("Manual task reminder: %(title)s")
        title = self.category.name if self.category else self.description
        return template % {"title": title or _("Manual task")}

    def _notification_body(self) -> str:
        lines = [self.description or ""]
        if self.scheduled_start:
            lines.append(
                _("Starts: %(start)s")
                % {"start": self._format_datetime(self.scheduled_start)}
            )
        if self.scheduled_end:
            lines.append(
                _("Ends: %(end)s")
                % {"end": self._format_datetime(self.scheduled_end)}
            )
        if self.node_id:
            lines.append(_("Node: %(node)s") % {"node": self.node})
        if self.location_id:
            lines.append(_("Location: %(location)s") % {"location": self.location})
        return "\n".join(line for line in lines if line)

    def send_notification_email(self, trigger: str) -> bool:
        recipients = self.resolve_notification_recipients()
        if not recipients:
            return False
        subject = self._notification_subject(trigger)
        body = self._notification_body()
        if self.node_id and self.node:
            self.node.send_mail(subject, body, recipients)
        else:
            mailer.send(subject, body, recipients)
        return True

    def _schedule_notification_task(
        self, trigger: str, eta: timezone.datetime | None = None
    ) -> None:
        from apps.teams.tasks import send_manual_task_notification

        kwargs = {"manual_task_id": self.pk, "trigger": trigger}
        if eta is None:
            send_manual_task_notification.apply_async(kwargs=kwargs)
        else:
            send_manual_task_notification.apply_async(kwargs=kwargs, eta=eta)

    def schedule_notifications(self) -> None:
        if not self.enable_notifications:
            return
        if not self._is_celery_enabled():
            return
        if not mailer.can_send_email():
            return
        self._schedule_notification_task("immediate")
        if not self.scheduled_start:
            return
        start = self.scheduled_start
        if timezone.is_naive(start):
            start = timezone.make_aware(start, timezone.get_current_timezone())
        now = timezone.now()
        reminders: Sequence[tuple[str, timezone.datetime]] = (
            ("24h", start - timedelta(hours=24)),
            ("3h", start - timedelta(hours=3)),
        )
        for trigger, eta in reminders:
            if eta <= now:
                continue
            self._schedule_notification_task(trigger, eta=eta)

    # Reservation helpers --------------------------------------------

    def _iter_reservation_users(self) -> Iterator[CoreUser]:
        if self.assigned_user_id and self.assigned_user:
            yield self.assigned_user
        if self.assigned_group_id and self.assigned_group:
            for user in self.assigned_group.user_set.filter(is_active=True):
                yield user
        node = self.node
        if not node:
            return
        outbox = getattr(node, "email_outbox", None)
        if not outbox:
            return
        owner = outbox.owner
        if owner is None:
            return
        if isinstance(owner, CoreUser):
            yield owner
        elif isinstance(owner, CoreSecurityGroup):
            for user in owner.user_set.filter(is_active=True):
                yield user

    def resolve_reservation_credentials(self):
        from apps.energy.models import CustomerAccount
        from apps.core.models import RFID

        account: CustomerAccount | None = None
        rfid: RFID | None = None

        for candidate in self._iter_reservation_users():
            try:
                account = candidate.customer_account
            except CustomerAccount.DoesNotExist:
                account = None
            if not account:
                continue
            rfid = account.rfids.filter(allowed=True).order_by("pk").first()
            if rfid:
                break
        if not rfid or not account:
            return None, None, ""
        return account, rfid, rfid.rfid

    def create_cp_reservation(self):
        from apps.ocpp.models import CPReservation

        if not self.location_id or not self.location:
            raise ValidationError(
                {"location": _("Select a location before reserving a connector.")}
            )
        if not self.scheduled_start or not self.scheduled_end:
            raise ValidationError(
                {
                    "scheduled_start": _("Provide a full schedule before reserving."),
                    "scheduled_end": _("Provide a full schedule before reserving."),
                }
            )
        duration_seconds = (self.scheduled_end - self.scheduled_start).total_seconds()
        duration_minutes = max(1, int(ceil(duration_seconds / 60)))
        account, rfid, id_tag = self.resolve_reservation_credentials()
        if not id_tag:
            raise ValidationError(
                _("Unable to determine an RFID tag for the assigned contacts.")
            )

        reservation = CPReservation(
            location=self.location,
            start_time=self.scheduled_start,
            duration_minutes=duration_minutes,
            account=account,
            rfid=rfid,
            id_tag=id_tag,
        )
        reservation.full_clean(exclude=["connector"])
        reservation.save()
        reservation.send_reservation_request()
        return reservation

    def save(self, *args, **kwargs):
        track_fields = (
            "enable_notifications",
            "scheduled_start",
            "scheduled_end",
            "assigned_user_id",
            "assigned_group_id",
        )
        previous = None
        if self.pk:
            previous = (
                type(self)
                .all_objects.filter(pk=self.pk)
                .values(*track_fields)
                .first()
            )
        super().save(*args, **kwargs)
        should_schedule = False
        if self.enable_notifications:
            if not previous:
                should_schedule = True
            else:
                for field in track_fields:
                    old_value = previous.get(field)
                    new_value = getattr(self, field)
                    if old_value != new_value:
                        should_schedule = True
                        break
        if should_schedule:
            self.schedule_notifications()

