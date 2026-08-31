import logging

from django.core.exceptions import ValidationError
from django.db import models

from apps.users.models import Profile as CoreProfile
from apps.sigils.fields import SigilShortAutoField

logger = logging.getLogger(__name__)


class EmailInbox(CoreProfile):
    """Credentials and configuration for connecting to an email mailbox."""

    owner_required = True
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
                            decoded.append(text.decode(candidate, errors="ignore"))
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
