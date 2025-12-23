from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.core.models import EmailArtifact
from apps.emails.models.inbox import EmailInbox

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

    class Meta:
        verbose_name = _("Email Collector")
        verbose_name_plural = _("Email Collectors")
        db_table = "core_emailcollector"

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
