import logging
import re

from django.db import IntegrityError, models
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
    notification_recipe = models.ForeignKey(
        "recipes.Recipe",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="email_collectors",
        help_text="Optional recipe to execute after the selected notification action.",
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
                        "Failed email notification for collector %s; "
                        "Failed to send notification for collector %s",
                        self.pk,
                        self.pk,
                    )

        recipe = self.notification_recipe
        if recipe is None:
            return

        recipe.execute(
            subject=rendered_subject,
            message=rendered_message,
            sender=context.get("sender", ""),
            body=context.get("body", ""),
            date=context.get("date", ""),
            sigils=sigils,
        )

    def collect(self, limit: int = 10) -> None:
        """Poll inboxes and store artifacts for messages not already recorded."""
        if not self.is_enabled:
            return

        messages = self.search_messages(limit=limit)
        for msg in messages:
            fp = EmailArtifact.fingerprint_for(
                msg.get("subject", ""), msg.get("from", ""), msg.get("body", "")
            )
            sigils = self._parse_sigils(msg.get("body", ""))

            try:
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
            except IntegrityError:
                continue
            if not created:
                continue

            try:
                self._notify_for_message(msg, sigils)
            except Exception:
                logger.exception("Failed to send notification for collector %s", self.pk)
