from __future__ import annotations

from .base import *

class CPFirmware(Entity):
    """Persisted firmware packages associated with charge points."""

    class Source(models.TextChoices):
        DOWNLOAD = "download", _("Downloaded")
        UPLOAD = "upload", _("Uploaded")

    name = models.CharField(_("Name"), max_length=200, blank=True)
    description = models.TextField(_("Description"), blank=True)
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.DOWNLOAD,
        verbose_name=_("Source"),
    )
    source_node = models.ForeignKey(
        Node,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="downloaded_firmware",
        verbose_name=_("Source node"),
    )
    source_charger = models.ForeignKey(
        "Charger",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="downloaded_firmware",
        verbose_name=_("Source charge point"),
    )
    content_type = models.CharField(
        _("Content type"),
        max_length=100,
        default="application/octet-stream",
        blank=True,
    )
    filename = models.CharField(_("Filename"), max_length=255, blank=True)
    payload_json = models.JSONField(null=True, blank=True)
    payload_binary = models.BinaryField(null=True, blank=True)
    payload_encoding = models.CharField(_("Encoding"), max_length=32, blank=True)
    payload_size = models.PositiveIntegerField(_("Payload size"), default=0)
    checksum = models.CharField(_("Checksum"), max_length=128, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    download_vendor_id = models.CharField(
        _("Vendor ID"), max_length=255, blank=True
    )
    download_message_id = models.CharField(
        _("Message ID"), max_length=64, blank=True
    )
    downloaded_at = models.DateTimeField(_("Downloaded at"), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("CP Firmware")
        verbose_name_plural = _("CP Firmware")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        label = self.name or self.filename or ""
        if label:
            return label
        return f"Firmware #{self.pk}" if self.pk else "Firmware"

    def save(self, *args, **kwargs):
        if self.filename:
            self.filename = os.path.basename(self.filename)
        payload_bytes = self.get_payload_bytes()
        self.payload_size = len(payload_bytes)
        if payload_bytes:
            self.checksum = hashlib.sha256(payload_bytes).hexdigest()
        elif not self.checksum:
            self.checksum = ""
        if not self.content_type:
            if self.payload_binary:
                self.content_type = "application/octet-stream"
            elif self.payload_json is not None:
                self.content_type = "application/json"
        if not self.payload_encoding:
            self.payload_encoding = ""
        super().save(*args, **kwargs)

    def get_payload_bytes(self) -> bytes:
        if self.payload_binary:
            return bytes(self.payload_binary)
        if self.payload_json is not None:
            try:
                return json.dumps(
                    self.payload_json,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            except (TypeError, ValueError):
                return str(self.payload_json).encode("utf-8")
        return b""

    @property
    def has_binary(self) -> bool:
        return bool(self.payload_binary)

    @property
    def has_json(self) -> bool:
        return self.payload_json is not None
