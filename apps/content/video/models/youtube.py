"""YouTube metadata models."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class YoutubeChannel(Entity):
    """YouTube channel reference tracked within Arthexis."""

    title = models.CharField(max_length=255)
    channel_id = models.CharField(
        max_length=64,
        unique=True,
        help_text=_("YouTube channel identifier (for example UC1234abcd)."),
    )
    handle = models.CharField(
        max_length=64,
        blank=True,
        help_text=_("Optional YouTube handle (for example @arthexis)."),
    )
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["title"]
        verbose_name = _("YouTube Channel")
        verbose_name_plural = _("YouTube Channels")
        constraints = [
            models.UniqueConstraint(
                fields=["handle"],
                condition=~models.Q(handle=""),
                name="youtubechannel_handle_unique",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover
        title = (self.title or "").strip()
        if title:
            return title
        handle = self.get_handle(include_at=True)
        if handle:
            return handle
        if self.channel_id:
            return self.channel_id
        return super().__str__()

    def save(self, *args, **kwargs):
        self.channel_id = (self.channel_id or "").strip()
        self.handle = (self.handle or "").strip()
        super().save(*args, **kwargs)

    def get_handle(self, *, include_at: bool = False) -> str:
        """Return the normalized handle, optionally prefixed with ``@``."""

        handle = (self.handle or "").strip().lstrip("@")
        if include_at and handle:
            return f"@{handle}"
        return handle

    def get_channel_url(self) -> str:
        """Return the best YouTube URL for the channel."""

        handle = self.get_handle()
        if handle:
            return f"https://www.youtube.com/@{handle}"
        if self.channel_id:
            return f"https://www.youtube.com/channel/{self.channel_id}"
        return ""
