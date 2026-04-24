import fnmatch
import hashlib
import mimetypes
import uuid
from datetime import datetime
from pathlib import Path

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


def media_bucket_slug() -> str:
    return uuid.uuid4().hex


def media_source_file_slug() -> str:
    return uuid.uuid4().hex


def media_file_path(instance: "MediaFile", filename: str) -> str:
    bucket_slug = instance.bucket.slug or "bucket"
    return f"protocols/buckets/{bucket_slug}/{Path(filename).name}"


def media_source_file_path(instance: "MediaSourceFile", filename: str) -> str:
    source_slug = instance.slug or "source"
    return f"protocols/source-files/{source_slug}/{Path(filename).name}"


def file_sha256(file_obj) -> str:
    digest = hashlib.sha256()
    source = getattr(file_obj, "_file", None) or file_obj
    if source is file_obj and hasattr(file_obj, "open"):
        try:
            file_obj.open("rb")
        except (FileNotFoundError, OSError, ValueError):
            return ""
        source = getattr(file_obj, "_file", None) or file_obj
    if hasattr(source, "seek"):
        source.seek(0)
    if hasattr(source, "chunks"):
        for chunk in source.chunks():
            digest.update(chunk)
    else:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    if hasattr(source, "seek"):
        source.seek(0)
    return digest.hexdigest()


def _file_content_type(file_obj) -> str:
    content_type = getattr(file_obj, "content_type", "")
    if not content_type:
        content_type = getattr(getattr(file_obj, "file", None), "content_type", "")
    if not content_type:
        content_type = mimetypes.guess_type(getattr(file_obj, "name", ""))[0] or ""
    return content_type


def _file_size(file_obj) -> int:
    try:
        return getattr(file_obj, "size", 0) or 0
    except (FileNotFoundError, OSError, ValueError):
        return 0


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
        return [
            value.strip()
            for value in self.allowed_patterns.splitlines()
            if value.strip()
        ]

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


class MediaSourceFile(Entity):
    class SourceType(models.TextChoices):
        MSE_SET = "mse_set", _("Magic Set Editor set")
        ARCHIVE = "archive", _("Archive")
        OTHER = "other", _("Other")

    name = models.CharField(_("Name"), max_length=120, blank=True, default="")
    slug = models.SlugField(
        _("Source Path"), max_length=64, default=media_source_file_slug, unique=True
    )
    source_type = models.CharField(
        _("Source type"),
        max_length=30,
        choices=SourceType.choices,
        default=SourceType.MSE_SET,
    )
    file = models.FileField(_("File"), upload_to=media_source_file_path)
    original_name = models.CharField(
        _("Original name"), max_length=255, blank=True, default=""
    )
    content_type = models.CharField(
        _("Content type"), max_length=255, blank=True, default=""
    )
    size = models.BigIntegerField(_("Size (bytes)"), default=0)
    checksum_sha256 = models.CharField(
        _("SHA-256 checksum"), max_length=64, blank=True, default="", db_index=True
    )
    source_uri = models.CharField(
        _("Source URI"),
        max_length=512,
        blank=True,
        default="",
        help_text=_(
            "Original local path, upload source, or external URI for provenance."
        ),
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Media Source File")
        verbose_name_plural = _("Media Source Files")
        ordering = ("-uploaded_at", "pk")
        db_table = "protocols_mediasourcefile"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name or self.original_name or Path(self.file.name).name

    def save(self, *args, **kwargs):
        previous = self._previous_version()
        file_changed = self._file_has_changed(previous)
        if self.file and (
            not self.original_name
            or (
                file_changed
                and previous
                and self.original_name == previous.original_name
            )
        ):
            self.original_name = Path(self.file.name).name
        if self.file and (
            not self.name or (file_changed and previous and self.name == previous.name)
        ):
            self.name = Path(self.original_name or self.file.name).stem
        if self.file and (
            not self.size or (file_changed and previous and self.size == previous.size)
        ):
            self.size = _file_size(self.file)
        if self.file and (
            not self.content_type
            or (
                file_changed and previous and self.content_type == previous.content_type
            )
        ):
            self.content_type = _file_content_type(self.file)
        if self.file and (
            not self.checksum_sha256
            or (
                file_changed
                and previous
                and self.checksum_sha256 == previous.checksum_sha256
            )
        ):
            self.checksum_sha256 = file_sha256(self.file)
        super().save(*args, **kwargs)

    def _previous_version(self):
        if not self.pk:
            return None
        return (
            type(self)
            .objects.filter(pk=self.pk)
            .only("file", "name", "original_name")
            .first()
        )

    def _file_has_changed(self, previous) -> bool:
        if not self.file:
            return False
        if getattr(self.file, "_committed", True) is False:
            return True
        if previous is None:
            return True
        return previous.file.name != self.file.name


class MediaFile(Entity):
    bucket = models.ForeignKey(
        MediaBucket,
        on_delete=models.CASCADE,
        related_name="files",
        verbose_name=_("Bucket"),
    )
    file = models.FileField(upload_to=media_file_path)
    source_file = models.ForeignKey(
        MediaSourceFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="derived_files",
        verbose_name=_("Source file"),
        help_text=_("Archive or source package this media file was derived from."),
    )
    source_member = models.CharField(
        _("Source member"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Path or member name inside the source file, when applicable."),
    )
    original_name = models.CharField(
        _("Original name"), max_length=255, blank=True, default=""
    )
    content_type = models.CharField(
        _("Content type"), max_length=255, blank=True, default=""
    )
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
