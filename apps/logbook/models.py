from __future__ import annotations

import secrets
import string
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import models
from django.utils import timezone

from apps.content.models import ContentSample
from apps.core.entity import Entity


def _generate_secret(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "-_.~"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _logbook_upload_path(instance: "LogbookLogAttachment", filename: str) -> str:
    return f"logbook/logs/{instance.entry.secret}-{filename}"


def _debug_upload_path(instance: "LogbookEntry", filename: str) -> str:
    return f"logbook/debug/{instance.secret}-{filename}"


class LogbookEntry(Entity):
    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logbook_entries",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logbook_entries",
    )
    secret = models.CharField(max_length=16, unique=True, editable=False)
    title = models.CharField(max_length=200)
    report = models.TextField()
    event_at = models.DateTimeField(null=True, blank=True)
    debug_info = models.JSONField(null=True, blank=True)
    debug_document = models.FileField(upload_to=_debug_upload_path, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    content_samples = models.ManyToManyField(
        ContentSample,
        blank=True,
        related_name="logbook_entries",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Logbook Entry"
        verbose_name_plural = "Logbook Entries"

    def save(self, *args, **kwargs):
        if not self.secret:
            self.secret = self.generate_secret()
        if self.node_id is None:
            from apps.nodes.models import Node

            self.node = Node.get_local()
        if self.event_at is None:
            self.event_at = self.created_at or timezone.now()
        super().save(*args, **kwargs)

    @staticmethod
    def generate_secret() -> str:
        secret = _generate_secret()
        attempts = 0
        while LogbookEntry.objects.filter(secret=secret).exists():
            attempts += 1
            if attempts > 5:
                length = min(16, 12 + attempts)
            else:
                length = 16
            secret = _generate_secret(length)
        return secret

    @property
    def event_date(self):
        return self.event_at or self.created_at

    def get_absolute_url(self):
        return f"/logbook/{self.secret}/"

    def attach_debug_document(self, uploaded_file) -> None:
        if not uploaded_file:
            return
        self.debug_document.save(uploaded_file.name, uploaded_file, save=True)

    def add_image_sample(self, sample: ContentSample | None) -> None:
        if sample:
            self.content_samples.add(sample)


class LogbookLogAttachment(Entity):
    entry = models.ForeignKey(
        LogbookEntry, on_delete=models.CASCADE, related_name="log_attachments"
    )
    file = models.FileField(upload_to=_logbook_upload_path)
    original_name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["original_name"]
        verbose_name = "Logbook Log Attachment"
        verbose_name_plural = "Logbook Log Attachments"

    def store_copy(self, source_path: Path) -> None:
        with source_path.open("rb") as handle:
            content = handle.read()
        self.file.save(
            _logbook_upload_path(self, source_path.name),
            ContentFile(content),
            save=False,
        )
        if hasattr(default_storage, "path"):
            stored_path = Path(default_storage.path(self.file.name))
            self.size = stored_path.stat().st_size if stored_path.exists() else len(content)
        else:
            self.size = len(content)
        self.save(update_fields=["file", "size"])
