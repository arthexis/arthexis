from django.db import models
from bind.models import Entity
from django.contrib.sites.models import Site
from nodes.models import NodeRole
from django.apps import apps as django_apps
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from importlib import import_module
from django.urls import URLPattern


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


class ModuleManager(models.Manager):
    def get_by_natural_key(self, role: str, path: str):
        return self.get(node_role__name=role, path=path)


class Module(Entity):
    node_role = models.ForeignKey(
        NodeRole, on_delete=models.CASCADE, related_name="modules",
    )
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="modules",
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
    favicon = models.ImageField(upload_to="modules/favicons/", blank=True)

    objects = ModuleManager()

    class Meta:
        verbose_name = _("Module")
        verbose_name_plural = _("Modules")
        unique_together = ("node_role", "path")

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.node_role.name, self.path)

    natural_key.dependencies = ["nodes.NodeRole"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.application.name} ({self.path})"

    @property
    def menu_label(self) -> str:
        return self.menu or self.application.name

    def save(self, *args, **kwargs):
        if not self.path:
            self.path = f"/{slugify(self.application.name)}/"
        super().save(*args, **kwargs)

    def create_landings(self):
        try:
            urlconf = import_module(f"{self.application.name}.urls")
        except Exception:
            try:
                urlconf = import_module(f"{self.application.name.lower()}.urls")
            except Exception:
                Landing.objects.get_or_create(
                    module=self,
                    path=self.path,
                    defaults={"label": self.application.name},
                )
                return
        patterns = getattr(urlconf, "urlpatterns", [])
        created = False

        def _walk(patterns, prefix=""):
            nonlocal created
            for pattern in patterns:
                if isinstance(pattern, URLPattern):
                    callback = pattern.callback
                    if getattr(callback, "landing", False):
                        Landing.objects.get_or_create(
                            module=self,
                            path=f"{self.path}{prefix}{str(pattern.pattern)}",
                            defaults={
                                "label": getattr(
                                    callback,
                                    "landing_label",
                                    callback.__name__.replace("_", " ").title(),
                                )
                            },
                        )
                        created = True
                else:
                    _walk(pattern.url_patterns, prefix=f"{prefix}{str(pattern.pattern)}")

        _walk(patterns)

        if not created:
            Landing.objects.get_or_create(
                module=self, path=self.path, defaults={"label": self.application.name}
            )


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


class Landing(Entity):
    module = models.ForeignKey(
        Module, on_delete=models.CASCADE, related_name="landings"
    )
    path = models.CharField(max_length=200)
    label = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    class Meta:
        unique_together = ("module", "path")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.label} ({self.path})"


from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=Module)
def _create_landings(
    sender, instance, created, raw, **kwargs
):  # pragma: no cover - simple handler
    if created and not raw:
        instance.create_landings()
