from django.db import models
from django.contrib.sites.models import Site
from django.apps import apps as django_apps
from django.utils.text import slugify

if not hasattr(Site, "is_seed_data"):
    Site.add_to_class("is_seed_data", models.BooleanField(default=False))


class Application(models.Model):
    site = models.ForeignKey(
        Site, on_delete=models.CASCADE, related_name="applications"
    )
    name = models.CharField(max_length=100)
    path = models.CharField(
        max_length=100,
        help_text="Base path for the app, starting with /",
        blank=True,
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        unique_together = ("site", "path")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.name} ({self.path})"

    @property
    def installed(self) -> bool:
        return django_apps.is_installed(self.name)

    def save(self, *args, **kwargs):
        if not self.path:
            self.path = f"/{slugify(self.name)}/"
        super().save(*args, **kwargs)


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
