from django.db import models
from django.contrib.sites.models import Site
from django.apps import apps as django_apps
from django.utils.text import slugify


class Application(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    @property
    def installed(self) -> bool:
        return django_apps.is_installed(self.name)


class SiteApplication(models.Model):
    site = models.ForeignKey(
        Site, on_delete=models.CASCADE, related_name="site_applications"
    )
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="site_applications"
    )
    path = models.CharField(
        max_length=100,
        help_text="Base path for the app, starting with /",
        blank=True,
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        unique_together = ("site", "path")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.application.name} ({self.path})"

    def save(self, *args, **kwargs):
        if not self.path:
            self.path = f"/{slugify(self.application.name)}/"
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

