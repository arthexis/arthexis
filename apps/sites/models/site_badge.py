from __future__ import annotations

from django.contrib.sites.models import Site
from django.db import models

from apps.core.entity import Entity
from apps.media.models import MediaFile
from apps.media.utils import ensure_media_bucket


class SiteBadge(Entity):
    site = models.OneToOneField(Site, on_delete=models.CASCADE, related_name="badge")
    badge_color = models.CharField(max_length=7, default="#28a745")
    favicon_media = models.ForeignKey(
        MediaFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="site_badge_favicons",
        verbose_name="Favicon",
    )
    landing_override = models.ForeignKey(
        "Landing", null=True, blank=True, on_delete=models.SET_NULL
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Badge for {self.site.domain}"

    class Meta:
        verbose_name = "Site Badge"
        verbose_name_plural = "Site Badges"

    @property
    def favicon_file(self):
        if self.favicon_media and self.favicon_media.file:
            return self.favicon_media.file
        return None

    @property
    def favicon_url(self) -> str:
        file = self.favicon_file
        return file.url if file else ""


SITE_BADGE_FAVICON_BUCKET_SLUG = "sites-badge-favicons"
SITE_BADGE_FAVICON_ALLOWED_PATTERNS = "\n".join(["*.png", "*.ico", "*.svg", "*.jpg", "*.jpeg"])


def get_site_badge_favicon_bucket():
    return ensure_media_bucket(
        slug=SITE_BADGE_FAVICON_BUCKET_SLUG,
        name="Site Badge Favicons",
        allowed_patterns=SITE_BADGE_FAVICON_ALLOWED_PATTERNS,
        max_bytes=512 * 1024,
        expires_at=None,
    )
