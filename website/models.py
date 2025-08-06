from django.db import models
from django.contrib.sites.models import Site


class SiteBadge(models.Model):
    site = models.OneToOneField(Site, on_delete=models.CASCADE, related_name="badge")
    badge_color = models.CharField(max_length=7, default="#28a745")
    login_image = models.URLField(blank=True, help_text="Optional URL for login page image")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Badge for {self.site.domain}"
