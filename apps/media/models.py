import fnmatch
import uuid
from datetime import datetime
from pathlib import Path

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


def media_bucket_slug() -> str:
    return uuid.uuid4().hex


def media_file_path(instance: "MediaFile", filename: str) -> str:
    bucket_slug = instance.bucket.slug or "bucket"
    return f"protocols/buckets/{bucket_slug}/{Path(filename).name}"


class MediaBucket(Entity):
    name = models.CharField(_("Name"), max_length=100, blank=True, default="")
    slug = models.SlugField(
        _("Upload Path"), max_length=32, default=media_bucket_slug, unique=True
    )
    allowed_patterns = models.TextField(
        _("Allowed file patterns"),
        blank=True,
        default="",
        help_text=_("Newline-separated glob patterns (for example, *.zip or *.log)."),
    )
    max_bytes = models.BigIntegerField(
        _("Maximum size (bytes)"),
        null=True,
        blank=True,
        help_text=_("Reject uploads that exceed this limit."),
    )
    expires_at = models.DateTimeField(
        _("Accept uploads until"),
        null=True,
        blank=True,
        help_text=_("Stop accepting uploads after this timestamp."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Media Bucket")
        verbose_name_plural = _("Media Buckets")
        ordering = ("-created_at",)
        db_table = "protocols_mediabucket"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name or self.slug

    @property
    def patterns(self) -> list[str]:
        return [value.strip() for value in self.allowed_patterns.splitlines() if value.strip()]

    def is_expired(self, *, reference: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        reference_time = reference or timezone.now()
        return self.expires_at <= reference_time

    def allows_filename(self, filename: str) -> bool:
        if not filename:
            return False
        patterns = self.patterns
        if not patterns:
            return True
        name = Path(filename).name
        return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)

    def allows_size(self, size: int) -> bool:
        if not size:
            return True
        if self.max_bytes is None:
            return True
        return size <= self.max_bytes

    def relative_upload_path(self) -> str:
        return f"media/{self.slug}/"


class MediaFile(Entity):
    bucket = models.ForeignKey(
        MediaBucket, on_delete=models.CASCADE, related_name="files", verbose_name=_("Bucket")
    )
    file = models.FileField(upload_to=media_file_path)
    original_name = models.CharField(_("Original name"), max_length=255, blank=True, default="")
    content_type = models.CharField(_("Content type"), max_length=255, blank=True, default="")
    size = models.BigIntegerField(_("Size (bytes)"), default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Media File")
        verbose_name_plural = _("Media Files")
        ordering = ("-uploaded_at", "pk")
        db_table = "protocols_mediafile"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.original_name or Path(self.file.name).name

    def save(self, *args, **kwargs):
        if self.file and not self.original_name:
            self.original_name = Path(self.file.name).name
        if self.file and not self.size:
            self.size = getattr(self.file, "size", 0) or 0
        if self.file and not self.content_type:
            self.content_type = getattr(self.file, "content_type", "") or ""
        super().save(*args, **kwargs)
