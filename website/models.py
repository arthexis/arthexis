from django.db import models
from integrator.models import Entity
from django.contrib.sites.models import Site
from django.apps import apps as django_apps
from django.utils.text import slugify


class ApplicationManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class Application(Entity):
    name = models.CharField(max_length=100, unique=True)

    objects = ApplicationManager()

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    @property
    def installed(self) -> bool:
        return django_apps.is_installed(self.name)


class SiteApplicationManager(models.Manager):
    def get_by_natural_key(self, domain: str, path: str):
        return self.get(site__domain=domain, path=path)


class SiteApplication(Entity):
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
    menu = models.CharField(
        max_length=100,
        blank=True,
        help_text="Text used for the navbar pill; defaults to the application name.",
    )
    is_default = models.BooleanField(default=False)
    favicon = models.ImageField(upload_to="site_applications/favicons/", blank=True)

    objects = SiteApplicationManager()

    class Meta:
        unique_together = ("site", "path")

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.site.domain, self.path)

    natural_key.dependencies = ["sites.Site"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.application.name} ({self.path})"

    @property
    def menu_label(self) -> str:
        return self.menu or self.application.name

    def save(self, *args, **kwargs):
        if not self.path:
            self.path = f"/{slugify(self.application.name)}/"
        super().save(*args, **kwargs)


class SiteBadge(Entity):
    site = models.OneToOneField(Site, on_delete=models.CASCADE, related_name="badge")
    badge_color = models.CharField(max_length=7, default="#28a745")
    favicon = models.ImageField(upload_to="sites/favicons/", blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Badge for {self.site.domain}"


class SiteProxy(Site):
    class Meta:
        proxy = True
        app_label = "website"
        verbose_name = "Site"
        verbose_name_plural = "Sites"

