from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class EmailArtifact(Entity):
    """Store messages discovered by :class:`EmailCollector`."""

    collector = models.ForeignKey(
        "emails.EmailCollector", related_name="artifacts", on_delete=models.CASCADE
    )
    subject = models.CharField(max_length=255)
    sender = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    sigils = models.JSONField(default=dict)
    fingerprint = models.CharField(max_length=32)

    @staticmethod
    def fingerprint_for(subject: str, sender: str, body: str) -> str:
        import hashlib

        data = (subject or "") + (sender or "") + (body or "")
        hasher = hashlib.md5(data.encode("utf-8"), usedforsecurity=False)
        return hasher.hexdigest()

    class Meta:
        unique_together = ("collector", "fingerprint")
        verbose_name = "Email Artifact"
        verbose_name_plural = "Email Artifacts"
        ordering = ["-id"]


class EmailTransaction(Entity):
    """Persist inbound and outbound email messages and their metadata."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    DIRECTION_CHOICES = [
        (INBOUND, "Inbound"),
        (OUTBOUND, "Outbound"),
    ]

    STATUS_COLLECTED = "collected"
    STATUS_QUEUED = "queued"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_COLLECTED, "Collected"),
        (STATUS_QUEUED, "Queued"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
    ]

    direction = models.CharField(
        max_length=8,
        choices=DIRECTION_CHOICES,
        default=INBOUND,
        help_text="Whether the message originated from an inbox or is being sent out.",
    )
    status = models.CharField(
        max_length=9,
        choices=STATUS_CHOICES,
        default=STATUS_COLLECTED,
        help_text="Lifecycle stage for the stored email message.",
    )
    collector = models.ForeignKey(
        "emails.EmailCollector",
        null=True,
        blank=True,
        related_name="transactions",
        on_delete=models.SET_NULL,
        help_text="Collector that discovered this message, if applicable.",
    )
    inbox = models.ForeignKey(
        "emails.EmailInbox",
        null=True,
        blank=True,
        related_name="transactions",
        on_delete=models.SET_NULL,
        help_text="Inbox account the message was read from or will use for sending.",
    )
    outbox = models.ForeignKey(
        "emails.EmailOutbox",
        null=True,
        blank=True,
        related_name="transactions",
        on_delete=models.SET_NULL,
        help_text="Outbox configuration used to send the message, when known.",
    )
    message_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Message-ID header for threading and deduplication.",
    )
    thread_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Thread or conversation identifier, if provided by the provider.",
    )
    subject = models.CharField(max_length=998, blank=True)
    from_address = models.CharField(
        max_length=512,
        blank=True,
        help_text="From header as provided by the email message.",
    )
    sender_address = models.CharField(
        max_length=512,
        blank=True,
        help_text="Envelope sender address, if available.",
    )
    to_addresses = models.JSONField(
        default=list,
        blank=True,
        help_text="List of To recipient addresses.",
    )
    cc_addresses = models.JSONField(
        default=list,
        blank=True,
        help_text="List of Cc recipient addresses.",
    )
    bcc_addresses = models.JSONField(
        default=list,
        blank=True,
        help_text="List of Bcc recipient addresses.",
    )
    reply_to_addresses = models.JSONField(
        default=list,
        blank=True,
        help_text="List of Reply-To addresses from the message headers.",
    )
    headers = models.JSONField(
        default=dict,
        blank=True,
        help_text="Complete header map as parsed from the message.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional provider-specific metadata.",
    )
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    raw_content = models.TextField(
        blank=True,
        help_text="Raw RFC822 payload for the message, if stored.",
    )
    message_ts = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp supplied by the email headers.",
    )
    queued_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the message was queued for outbound delivery.",
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the message was sent or fully processed.",
    )
    error = models.TextField(
        blank=True,
        help_text="Failure details captured during processing, if any.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if not (self.collector_id or self.inbox_id or self.outbox_id):
            raise ValidationError(
                {"direction": _("Select an inbox, collector or outbox for the transaction.")}
            )
        if self.direction == self.INBOUND and not (self.collector_id or self.inbox_id):
            raise ValidationError(
                {"inbox": _("Inbound messages must reference a collector or inbox.")}
            )
        if self.direction == self.OUTBOUND and not (self.outbox_id or self.inbox_id):
            raise ValidationError(
                {"outbox": _("Outbound messages must reference an inbox or outbox.")}
            )

    def __str__(self):  # pragma: no cover - simple representation
        if self.subject:
            return self.subject
        if self.from_address:
            return self.from_address
        return super().__str__()

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Email Transaction"
        verbose_name_plural = "Email Transactions"
        indexes = [
            models.Index(fields=["message_id"], name="email_txn_msgid"),
            models.Index(fields=["direction", "status"], name="email_txn_dir_status"),
        ]


class EmailTransactionAttachment(Entity):
    """Attachment stored alongside an :class:`EmailTransaction`."""

    transaction = models.ForeignKey(
        EmailTransaction,
        related_name="attachments",
        on_delete=models.CASCADE,
    )
    filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=255, blank=True)
    content_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Identifier used for inline attachments.",
    )
    inline = models.BooleanField(
        default=False,
        help_text="Marks whether the attachment is referenced inline in the body.",
    )
    size = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Size of the decoded attachment payload in bytes.",
    )
    content = models.TextField(
        blank=True,
        help_text="Base64 encoded attachment payload.",
    )

    def __str__(self):  # pragma: no cover - simple representation
        if self.filename:
            return self.filename
        return super().__str__()

    class Meta:
        verbose_name = "Email Attachment"
        verbose_name_plural = "Email Attachments"
