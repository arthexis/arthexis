"""Reference models and managers."""

from __future__ import annotations

import hashlib
import uuid
from io import BytesIO
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityManager
from apps.content.storage.models import MediaFile
from apps.content.storage.utils import create_media_file, ensure_media_bucket


class ReferenceManager(EntityManager):
    """Manager for reference natural key lookups."""

    def get_by_natural_key(self, alt_text: str, value: str | None = None):
        """Resolve a natural key for fixture deserialization."""

        filters = {"alt_text": alt_text}
        if value is not None:
            filters["value"] = value
        return self.get(**filters)


class Reference(Entity):
    """Store a piece of reference content which can be text or an image."""

    TEXT = "text"
    IMAGE = "image"
    CONTENT_TYPE_CHOICES = [
        (TEXT, "Text"),
        (IMAGE, "Image"),
    ]

    content_type = models.CharField(
        max_length=5, choices=CONTENT_TYPE_CHOICES, default=TEXT
    )
    alt_text = models.CharField("Title / Alt Text", max_length=500)
    value = models.TextField(blank=True)
    file_media = models.ForeignKey(
        MediaFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reference_files",
        verbose_name=_("File"),
    )
    image_media = models.ForeignKey(
        MediaFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reference_images",
        verbose_name=_("Image"),
    )
    uses = models.PositiveIntegerField(default=0)
    method = models.CharField(max_length=50, default="qr")
    validated_url_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Validated URL",
        help_text="Timestamp of the last URL validation check.",
    )
    validation_status = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Validation Status",
        help_text="HTTP status code from the last URL validation attempt.",
    )
    include_in_footer = models.BooleanField(
        default=False, verbose_name="Include in Footer"
    )
    show_in_header = models.BooleanField(
        default=False, verbose_name="Show in Header"
    )
    FOOTER_PUBLIC = "public"
    FOOTER_PRIVATE = "private"
    FOOTER_STAFF = "staff"
    FOOTER_VISIBILITY_CHOICES = [
        (FOOTER_PUBLIC, "Public"),
        (FOOTER_PRIVATE, "Private"),
        (FOOTER_STAFF, "Staff"),
    ]
    footer_visibility = models.CharField(
        max_length=7,
        choices=FOOTER_VISIBILITY_CHOICES,
        default=FOOTER_PUBLIC,
        verbose_name="Footer Visibility",
    )
    footer_modules = models.ManyToManyField(
        "modules.Module",
        blank=True,
        related_name="footer_references",
        help_text=(
            "Optional module-specific footer rules. Leave blank to show in the "
            "general footer."
        ),
    )
    transaction_uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=True,
        db_index=True,
        verbose_name="Transaction UUID",
    )
    created = models.DateTimeField(auto_now_add=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="references",
        null=True,
        blank=True,
    )
    sites = models.ManyToManyField(
        "sites.Site",
        blank=True,
        related_name="references",
    )
    roles = models.ManyToManyField(
        "nodes.NodeRole",
        blank=True,
        related_name="references",
    )
    features = models.ManyToManyField(
        "nodes.NodeFeature",
        blank=True,
        related_name="references",
    )
    application = models.ForeignKey(
        "app.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="references",
        help_text="Optional application this reference belongs to.",
    )

    objects = ReferenceManager()

    def save(self, *args, **kwargs):
        """Save and auto-generate a QR image if needed."""

        if self.pk:
            original = type(self).all_objects.get(pk=self.pk)
            if original.transaction_uuid != self.transaction_uuid:
                raise ValidationError(
                    {"transaction_uuid": "Cannot modify transaction UUID"}
                )
        if not self.image_media and self.value:
            qr_code_module = _load_qrcode_module()
            if qr_code_module is not None:
                qr = qr_code_module.QRCode(box_size=10, border=4)
                qr.add_data(self.value)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                buffer = BytesIO()
                img.save(buffer, format="PNG")
                filename = hashlib.sha256(self.value.encode()).hexdigest()[:16] + ".png"
                bucket = get_reference_qr_bucket()
                upload = ContentFile(buffer.getvalue(), name=filename)
                self.image_media = create_media_file(
                    bucket=bucket, uploaded_file=upload, content_type="image/png"
                )
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.alt_text

    def natural_key(self):
        return (self.alt_text, self.value)

    def is_link_valid(self) -> bool:
        """Return ``True`` when the reference URL is valid."""

        if self.validation_status is None:
            return True
        return 200 <= self.validation_status < 400

    class Meta:
        db_table = "core_reference"
        verbose_name = _("Reference")
        verbose_name_plural = _("References")
        constraints = [
            models.UniqueConstraint(
                fields=["alt_text", "value"],
                name="links_reference_alt_text_value_uniq",
            )
        ]

    @property
    def image_file(self):
        if self.image_media and self.image_media.file:
            return self.image_media.file
        return None

    @property
    def image_url(self) -> str:
        file = self.image_file
        return file.url if file else ""

    @property
    def image(self):
        return self.image_file

    @property
    def file_file(self):
        if self.file_media and self.file_media.file:
            return self.file_media.file
        return None


REFERENCE_FILE_BUCKET_SLUG = "links-reference-files"


def _load_qrcode_module():
    """Return the optional ``qrcode`` module when available."""

    try:
        import qrcode
    except ModuleNotFoundError as exc:
        if exc.name == "qrcode":
            return None
        raise
    return qrcode


REFERENCE_FILE_ALLOWED_PATTERNS = "\n".join(
    [
        "*.pdf",
        "*.txt",
        "*.csv",
        "*.md",
        "*.doc",
        "*.docx",
        "*.xls",
        "*.xlsx",
        "*.ppt",
        "*.pptx",
        "*.zip",
        "*.png",
        "*.jpg",
        "*.jpeg",
    ]
)
REFERENCE_QR_BUCKET_SLUG = "links-reference-qr"
REFERENCE_QR_ALLOWED_PATTERNS = "\n".join(["*.png"])


def get_reference_file_bucket():
    """Return the media bucket used for reference file attachments."""

    return ensure_media_bucket(
        slug=REFERENCE_FILE_BUCKET_SLUG,
        name=_("Reference Files"),
        allowed_patterns=REFERENCE_FILE_ALLOWED_PATTERNS,
        max_bytes=10 * 1024 * 1024,
        expires_at=None,
    )


def get_reference_qr_bucket():
    """Return the media bucket used for generated reference QR images."""

    return ensure_media_bucket(
        slug=REFERENCE_QR_BUCKET_SLUG,
        name=_("Reference QR Images"),
        allowed_patterns=REFERENCE_QR_ALLOWED_PATTERNS,
        max_bytes=512 * 1024,
        expires_at=None,
    )


class ExperienceReference(Reference):
    """Proxy model for organizing reference admin behavior."""

    class Meta:
        proxy = True
        verbose_name = _("Reference")
        verbose_name_plural = _("References")
