import email
import imaplib
import re
from typing import Dict

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class EmailPattern(models.Model):
    """Pattern used to find and parse emails."""

    name = models.CharField(max_length=200)
    from_address = models.CharField(max_length=200, blank=True)
    to_address = models.CharField(max_length=200, blank=True)
    cc = models.CharField(max_length=200, blank=True)
    bcc = models.CharField(max_length=200, blank=True)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)

    class Meta:
        verbose_name = _("Email Pattern")
        verbose_name_plural = _("Email Patterns")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    @staticmethod
    def _regex_from_pattern(pattern: str) -> re.Pattern:
        """Convert a pattern with [sigils] into a regex."""

        escaped = re.escape(pattern)
        regex = re.sub(r"\\\[([^\\\]]+)\\\]", r"(?P<\1>.+)", escaped)
        return re.compile(regex, re.IGNORECASE)

    def matches(self, message: Dict[str, str]) -> Dict[str, str]:
        """Return variables extracted from ``message`` if it matches this pattern."""

        fields = {
            "from_address": self.from_address,
            "to_address": self.to_address,
            "cc": self.cc,
            "bcc": self.bcc,
            "subject": self.subject,
            "body": self.body,
        }
        result: Dict[str, str] = {}
        for field, pattern in fields.items():
            if pattern:
                regex = self._regex_from_pattern(pattern)
                value = message.get(field, "") or ""
                match = regex.search(value)
                if not match:
                    return {}
                result.update(match.groupdict())
        return result

    def test(self) -> Dict[str, str]:
        """Fetch emails from IMAP and return the first match's variables."""

        host = getattr(settings, "EMAIL_PATTERN_IMAP_HOST", None)
        user = getattr(settings, "EMAIL_PATTERN_IMAP_USER", None)
        password = getattr(settings, "EMAIL_PATTERN_IMAP_PASSWORD", None)
        if not all([host, user, password]):
            raise RuntimeError("IMAP credentials are not configured")

        with imaplib.IMAP4_SSL(host) as imap:
            imap.login(user, password)
            imap.select("INBOX")
            typ, data = imap.search(None, "ALL")
            for num in reversed(data[0].split()):
                typ, msg_data = imap.fetch(num, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                if msg.is_multipart():
                    body_parts = []
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body_parts.append(
                                part.get_payload(decode=True).decode(errors="ignore")
                            )
                    body = "\n".join(body_parts)
                else:
                    body = msg.get_payload(decode=True).decode(errors="ignore")
                msg_dict = {
                    "from_address": msg.get("From", ""),
                    "to_address": msg.get("To", ""),
                    "cc": msg.get("Cc", ""),
                    "bcc": msg.get("Bcc", ""),
                    "subject": msg.get("Subject", ""),
                    "body": body,
                }
                matches = self.matches(msg_dict)
                if matches:
                    return matches
        return {}

