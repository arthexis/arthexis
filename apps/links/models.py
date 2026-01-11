"""Models for storing shared external links and references."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from io import BytesIO
from urllib.parse import urlparse

import qrcode
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, models, transaction
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityManager
from apps.leads.models import Lead
from apps.media.models import MediaFile
from apps.media.utils import create_media_file, ensure_media_bucket


def _generate_qr_slug() -> str:
    return secrets.token_urlsafe(6).rstrip("=")


def _is_valid_redirect_target(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return url_has_allowed_host_and_scheme(value, allowed_hosts={parsed.netloc})
    if not parsed.scheme and not parsed.netloc and value.startswith("/"):
        return True
    return False


class ReferenceManager(EntityManager):
    def get_by_natural_key(self, alt_text: str):
        return self.get(alt_text=alt_text)


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

    objects = ReferenceManager()

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.get(pk=self.pk)
            if original.transaction_uuid != self.transaction_uuid:
                raise ValidationError(
                    {"transaction_uuid": "Cannot modify transaction UUID"}
                )
        if not self.image_media and self.value:
            qr = qrcode.QRCode(box_size=10, border=4)
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

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.alt_text

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.alt_text,)

    def is_link_valid(self) -> bool:
        """Return ``True`` when the reference URL is valid."""

        if self.validation_status is None:
            return True
        return 200 <= self.validation_status < 400

    class Meta:
        db_table = "core_reference"
        verbose_name = _("Reference")
        verbose_name_plural = _("References")

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
    return ensure_media_bucket(
        slug=REFERENCE_FILE_BUCKET_SLUG,
        name=_("Reference Files"),
        allowed_patterns=REFERENCE_FILE_ALLOWED_PATTERNS,
        max_bytes=10 * 1024 * 1024,
        expires_at=None,
    )


def get_reference_qr_bucket():
    return ensure_media_bucket(
        slug=REFERENCE_QR_BUCKET_SLUG,
        name=_("Reference QR Images"),
        allowed_patterns=REFERENCE_QR_ALLOWED_PATTERNS,
        max_bytes=512 * 1024,
        expires_at=None,
    )


class ExperienceReference(Reference):
    class Meta:
        proxy = True
        verbose_name = _("Reference")
        verbose_name_plural = _("References")

class QRRedirect(Entity):
    """Short-slug QR redirect that can be updated without changing the slug."""

    slug = models.SlugField(
        max_length=32,
        unique=True,
        blank=True,
        help_text=_("Short, persistent slug used for QR links."),
    )
    target_url = models.CharField(
        max_length=500,
        help_text=_("Destination URL or absolute path."),
    )
    title = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("Optional display title for the public view."),
    )
    text_above = models.TextField(
        blank=True,
        help_text=_("Optional text to show above the embedded content."),
    )
    text_below = models.TextField(
        blank=True,
        help_text=_("Optional text to show below the embedded content."),
    )
    is_public = models.BooleanField(
        default=False,
        help_text=_("Controls visibility in the public QR listing."),
    )
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("QR Redirect")
        verbose_name_plural = _("QR Redirects")
        ordering = ("slug",)

    def __str__(self) -> str:  # pragma: no cover - representation
        return self.title or self.slug

    def clean(self):
        super().clean()
        if self.target_url and not _is_valid_redirect_target(self.target_url.strip()):
            raise ValidationError(
                {"target_url": _("Enter a valid http(s) URL or absolute path.")}
            )

    def save(self, *args, **kwargs):
        self.target_url = (self.target_url or "").strip()
        if self.slug or self.pk is not None:
            super().save(*args, **kwargs)
            return
        for _ in range(5):
            self.slug = _generate_qr_slug()
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                    return
            except IntegrityError:
                self.pk = None
        raise IntegrityError("Failed to generate a unique slug after multiple attempts.")

    def redirect_path(self) -> str:
        return reverse("links:qr-redirect", args=[self.slug])

    def public_path(self) -> str:
        return reverse("links:qr-redirect-public", args=[self.slug])


class QRRedirectLead(Lead):
    qr_redirect = models.ForeignKey(
        QRRedirect,
        on_delete=models.CASCADE,
        related_name="leads",
    )
    target_url = models.TextField(blank=True)

    class Meta:
        verbose_name = _("QR Redirect Lead")
        verbose_name_plural = _("QR Redirect Leads")


__all__ = [
    "ExperienceReference",
    "QRRedirect",
    "QRRedirectLead",
    "Reference",
    "ReferenceManager",
]
