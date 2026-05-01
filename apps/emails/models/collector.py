import logging
import re

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.core.models import EmailArtifact
from apps.emails.models.inbox import EmailInbox

logger = logging.getLogger(__name__)


class EmailCollector(Entity):
    """Search an inbox for matching messages and extract data via sigils."""

    name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional label to identify this collector.",
    )
    inbox = models.ForeignKey(
        EmailInbox,
        related_name="collectors",
        on_delete=models.CASCADE,
    )
    additional_inboxes = models.ManyToManyField(
        EmailInbox,
        related_name="secondary_collectors",
        blank=True,
        help_text="Optional additional inbox accounts monitored by this collector.",
    )
    subject = models.CharField(max_length=255, blank=True)
    sender = models.CharField(max_length=255, blank=True)
    body = models.CharField(max_length=255, blank=True)
    fragment = models.CharField(
        max_length=255,
        blank=True,
        help_text="Pattern with [sigils] to extract values from the body.",
    )
    odoo_profile = models.ForeignKey(
        "odoo.OdooEmployee",
        related_name="email_collectors",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional Odoo account used to validate customer fields.",
    )
    odoo_customer_name_sigil = models.CharField(
        max_length=64,
        blank=True,
        default="customer_name",
        help_text="Parsed body sigil used as the Odoo customer lookup name.",
    )
    odoo_customer_name = models.CharField(max_length=255, blank=True, editable=False)
    odoo_customer_address = models.TextField(blank=True, editable=False)
    odoo_customer_phone = models.CharField(max_length=64, blank=True, editable=False)
    odoo_customer_checked_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
    )
    use_regular_expressions = models.BooleanField(
        default=False,
        help_text="Treat subject, sender and body filters as regular expressions (case-insensitive).",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Disable to exclude this collector from automatic runs and admin totals.",
    )
    NOTIFY_EMAIL = "email"
    NOTIFY_NET_MESSAGE = "net_message"
    NOTIFY_NONE = "none"
    NOTIFY_POPUP = "popup"
    NOTIFICATION_MODE_CHOICES = [
        (NOTIFY_EMAIL, "Email"),
        (NOTIFY_NET_MESSAGE, "Net message"),
        (NOTIFY_NONE, "Nothing"),
        (NOTIFY_POPUP, "Local popup"),
    ]
    notification_mode = models.CharField(
        max_length=16,
        choices=NOTIFICATION_MODE_CHOICES,
        default=NOTIFY_NONE,
        help_text="Action to run after collecting a new email artifact.",
    )
    notification_subject = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional notification subject template. Supports [sigil] tokens.",
    )
    notification_message = models.TextField(
        blank=True,
        help_text="Optional notification message template. Supports [sigil] tokens.",
    )
    notification_recipients = models.CharField(
        max_length=255,
        blank=True,
        help_text="Comma-separated recipients used when notification mode is Email.",
    )

    class Meta:
        verbose_name = _("Email Collector")
        verbose_name_plural = _("Email Collectors")
        db_table = "core_emailcollector"

    def _parse_sigils(self, text: str) -> dict[str, str]:
        """Extract values from ``text`` according to ``fragment`` sigils."""
        if not self.fragment:
            return {}

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

    @staticmethod
    def _odoo_text(value) -> str:
        """Normalize Odoo RPC values for display and completeness checks."""
        if value in (None, False):
            return ""
        if isinstance(value, (list, tuple)):
            if len(value) > 1:
                return str(value[1] or "").strip()
            if value:
                return str(value[0] or "").strip()
            return ""
        return str(value).strip()

    @classmethod
    def _normalize_odoo_name(cls, value) -> str:
        """Return a normalized customer name for exact Odoo match checks."""
        return " ".join(cls._odoo_text(value).casefold().split())

    @classmethod
    def _format_odoo_address(cls, row: dict) -> str:
        """Return a compact address string from common Odoo partner fields."""
        city_line = " ".join(
            part
            for part in (
                cls._odoo_text(row.get("zip")),
                cls._odoo_text(row.get("city")),
            )
            if part
        )
        parts = [
            cls._odoo_text(row.get("street")),
            cls._odoo_text(row.get("street2")),
            city_line,
            cls._odoo_text(row.get("state_id")),
            cls._odoo_text(row.get("country_id")),
        ]
        return ", ".join(part for part in parts if part)

    def _odoo_customer_lookup_name(self, sigils: dict[str, str]) -> str:
        """Return the parsed name used to search Odoo for a customer."""
        candidates = [
            self.odoo_customer_name_sigil,
            "customer_name",
            "name",
            "customer",
        ]
        normalized = {
            str(key).lower(): str(value).strip() for key, value in sigils.items()
        }
        for candidate in candidates:
            key = str(candidate or "").strip().lower()
            if key and normalized.get(key):
                return normalized[key]
        return ""

    @property
    def odoo_customer_fields_complete(self) -> bool:
        """Whether the Odoo customer snapshot has the validation fields."""
        return bool(
            self.odoo_customer_name
            and self.odoo_customer_address
            and self.odoo_customer_phone
        )

    def _update_odoo_customer_snapshot(self, sigils: dict[str, str]) -> None:
        """Refresh read-only Odoo customer fields from parsed email sigils."""
        if not self.odoo_profile_id:
            return

        lookup_name = self._odoo_customer_lookup_name(sigils)
        self.odoo_customer_name = lookup_name
        self.odoo_customer_address = ""
        self.odoo_customer_phone = ""
        self.odoo_customer_checked_at = timezone.now()

        if lookup_name and getattr(self.odoo_profile, "odoo_uid", None):
            try:
                rows = self.odoo_profile.execute(
                    "res.partner",
                    "search_read",
                    [[("name", "=", lookup_name)]],
                    fields=[
                        "id",
                        "name",
                        "phone",
                        "mobile",
                        "street",
                        "street2",
                        "city",
                        "zip",
                        "state_id",
                        "country_id",
                    ],
                    limit=2,
                )
            except Exception:
                logger.exception(
                    "Failed Odoo customer validation for collector %s", self.pk
                )
            else:
                normalized_lookup = self._normalize_odoo_name(lookup_name)
                exact_rows = [
                    row
                    for row in rows
                    if self._normalize_odoo_name(row.get("name")) == normalized_lookup
                ]
                row = exact_rows[0] if len(exact_rows) == 1 else {}
                if len(exact_rows) > 1:
                    logger.warning(
                        "Skipped ambiguous Odoo customer validation for collector %s",
                        self.pk,
                    )
                if row:
                    self.odoo_customer_name = (
                        self._odoo_text(row.get("name")) or lookup_name
                    )
                    self.odoo_customer_phone = self._odoo_text(
                        row.get("phone")
                    ) or self._odoo_text(row.get("mobile"))
                    self.odoo_customer_address = self._format_odoo_address(row)

        self.save(
            update_fields=[
                "odoo_customer_name",
                "odoo_customer_address",
                "odoo_customer_phone",
                "odoo_customer_checked_at",
            ]
        )

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
        inboxes = [self.inbox, *self.additional_inboxes.all()]
        messages = []
        for inbox in inboxes:
            messages.extend(
                inbox.search_messages(
                    subject=self.subject,
                    from_address=self.sender,
                    body=self.body,
                    limit=limit,
                    use_regular_expressions=self.use_regular_expressions,
                )
            )
            if len(messages) >= limit:
                return messages[:limit]
        return messages

    @staticmethod
    def _render_notification_template(template: str, context: dict[str, str]) -> str:
        """Render ``template`` by replacing ``[token]`` placeholders from context."""
        if not template:
            return ""

        def _replace(match: re.Match[str]) -> str:
            key = match.group(1).strip()
            if not key:
                return ""
            return context.get(key.lower(), match.group(0))

        return re.sub(r"\[([^\]]+)\]", _replace, template)

    @staticmethod
    def _parse_recipients(raw: str) -> list[str]:
        """Return normalized recipient addresses from a comma-separated string."""
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _notify_for_message(self, msg: dict[str, str], sigils: dict[str, str]) -> None:
        """Dispatch collector notification according to ``notification_mode``."""
        mode = (self.notification_mode or self.NOTIFY_NONE).strip().lower()

        context = {
            "subject": msg.get("subject", ""),
            "sender": msg.get("from", ""),
            "body": msg.get("body", ""),
            "date": msg.get("date", ""),
        }
        context.update({str(key).lower(): str(value) for key, value in sigils.items()})

        subject_template = self.notification_subject or "[subject]"
        message_template = self.notification_message or "[body]"
        rendered_subject = self._render_notification_template(subject_template, context)
        rendered_subject = rendered_subject.replace("\n", " ").replace("\r", " ")
        rendered_message = self._render_notification_template(message_template, context)

        if mode == self.NOTIFY_POPUP:
            try:
                from apps.core.notifications import notify_async

                notify_async(rendered_subject, rendered_message)
            except Exception:
                logger.exception("Failed popup notification for collector %s", self.pk)

        if mode == self.NOTIFY_NET_MESSAGE:
            try:
                from apps.nodes.models import NetMessage

                NetMessage.broadcast(rendered_subject, rendered_message)
            except Exception:
                logger.exception("Failed net message notification for collector %s", self.pk)

        if mode == self.NOTIFY_EMAIL:
            recipients = self._parse_recipients(self.notification_recipients)
            if recipients:
                try:
                    from apps.emails import mailer

                    mailer.send(
                        subject=rendered_subject,
                        message=rendered_message,
                        recipient_list=recipients,
                        fail_silently=False,
                    )
                except Exception:
                    logger.exception(
                        "Failed to send email notification for collector %s",
                        self.pk,
                    )

    def collect(self, limit: int = 10) -> None:
        """Poll inboxes and store artifacts not already recorded.

        Args:
            limit: Maximum number of matching messages to fetch across inboxes.

        Returns:
            None.

        Note:
            Notification dispatch failures are logged and suppressed for popup,
            net-message, and email channels.
        """
        if not self.is_enabled:
            return

        messages = self.search_messages(limit=limit)
        odoo_snapshot_sigils = None
        for msg in messages:
            fp = EmailArtifact.fingerprint_for(
                msg.get("subject", ""), msg.get("from", ""), msg.get("body", "")
            )
            sigils = self._parse_sigils(msg.get("body", ""))

            _, created = EmailArtifact.objects.get_or_create(
                collector=self,
                fingerprint=fp,
                defaults={
                    "subject": msg.get("subject", ""),
                    "sender": msg.get("from", ""),
                    "body": msg.get("body", ""),
                    "sigils": sigils,
                },
            )
            if not created:
                continue

            if odoo_snapshot_sigils is None:
                odoo_snapshot_sigils = sigils

            try:
                self._notify_for_message(msg, sigils)
            except Exception:
                logger.exception("Failed to send notification for collector %s", self.pk)

        if odoo_snapshot_sigils is not None:
            try:
                self._update_odoo_customer_snapshot(odoo_snapshot_sigils)
            except Exception:
                logger.exception(
                    "Failed to update Odoo fields for collector %s", self.pk
                )
