import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import get_connection
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core import mailer
from apps.core.entity import Entity
from apps.core.models import EmailArtifact, Profile as CoreProfile
from apps.nodes.models import Node
from apps.sigils.fields import SigilShortAutoField

logger = logging.getLogger(__name__)


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
        "emails.EmailInbox",
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
