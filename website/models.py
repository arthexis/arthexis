from django.db import models
from django.contrib.sites.models import Site

if not hasattr(Site, "is_seed_data"):
    Site.add_to_class("is_seed_data", models.BooleanField(default=False))


class Application(models.Model):
    site = models.ForeignKey(
        Site, on_delete=models.CASCADE, related_name="applications"
    )
    name = models.CharField(max_length=100)
    path = models.CharField(
        max_length=100, help_text="Base path for the app, starting with /"
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        unique_together = ("site", "path")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.name} ({self.path})"


class SiteBadge(models.Model):
    site = models.OneToOneField(Site, on_delete=models.CASCADE, related_name="badge")
    badge_color = models.CharField(max_length=7, default="#28a745")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Badge for {self.site.domain}"


class SiteProxy(Site):
    class Meta:
        proxy = True
        app_label = "website"
        verbose_name = "Site"
        verbose_name_plural = "Sites"
