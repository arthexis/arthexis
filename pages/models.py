from django.db import models
from core.entity import Entity
from django.contrib.sites.models import Site
from nodes.models import NodeRole
from django.apps import apps as django_apps
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from importlib import import_module
from django.urls import URLPattern
from django.conf import settings
from django.contrib.contenttypes.models import ContentType


class ApplicationManager(models.Manager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class Application(Entity):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    objects = ApplicationManager()

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    @property
    def installed(self) -> bool:
        return django_apps.is_installed(self.name)

    @property
    def verbose_name(self) -> str:
        try:
            return django_apps.get_app_config(self.name).verbose_name
        except LookupError:
            return self.name


class ModuleManager(models.Manager):
    def get_by_natural_key(self, role: str, path: str):
        return self.get(node_role__name=role, path=path)


class Module(Entity):
    node_role = models.ForeignKey(
        NodeRole,
        on_delete=models.CASCADE,
        related_name="modules",
    )
    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="modules",
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
        role_name = None
        if getattr(self, "node_role_id", None):
            role_name = self.node_role.name
        return (role_name, self.path)

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
        normalized_module = self.path.strip("/")

        def _walk(patterns, prefix=""):
            nonlocal created
            for pattern in patterns:
                if isinstance(pattern, URLPattern):
                    callback = pattern.callback
                    if getattr(callback, "landing", False):
                        pattern_path = str(pattern.pattern)
                        relative = f"{prefix}{pattern_path}"
                        if normalized_module and relative.startswith(normalized_module):
                            full_path = f"/{relative}"
                            Landing.objects.update_or_create(
                                module=self,
                                path=full_path,
                                defaults={
                                    "label": getattr(
                                        callback,
                                        "landing_label",
                                        callback.__name__.replace("_", " ").title(),
                                    )
                                },
                            )
                        else:
                            full_path = f"{self.path}{relative}"
                            Landing.objects.get_or_create(
                                module=self,
                                path=full_path,
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
                    _walk(
                        pattern.url_patterns, prefix=f"{prefix}{str(pattern.pattern)}"
                    )

        _walk(patterns)

        if not created:
            Landing.objects.get_or_create(
                module=self, path=self.path, defaults={"label": self.application.name}
            )


class SiteBadge(Entity):
    site = models.OneToOneField(Site, on_delete=models.CASCADE, related_name="badge")
    badge_color = models.CharField(max_length=7, default="#28a745")
    favicon = models.ImageField(upload_to="sites/favicons/", blank=True)
    landing_override = models.ForeignKey(
        "Landing", null=True, blank=True, on_delete=models.SET_NULL
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Badge for {self.site.domain}"

    class Meta:
        verbose_name = "Site Badge"
        verbose_name_plural = "Site Badges"


class SiteProxy(Site):
    class Meta:
        proxy = True
        app_label = "pages"
        verbose_name = "Site"
        verbose_name_plural = "Sites"


class LandingManager(models.Manager):
    def get_by_natural_key(self, role: str, module_path: str, path: str):
        return self.get(
            module__node_role__name=role, module__path=module_path, path=path
        )


class Landing(Entity):
    module = models.ForeignKey(
        Module, on_delete=models.CASCADE, related_name="landings"
    )
    path = models.CharField(max_length=200)
    label = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    objects = LandingManager()

    class Meta:
        unique_together = ("module", "path")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.label} ({self.path})"

    def save(self, *args, **kwargs):
        existing = None
        if not self.pk:
            existing = (
                type(self).objects.filter(module=self.module, path=self.path).first()
            )
        if existing:
            self.pk = existing.pk
        super().save(*args, **kwargs)


class UserManual(Entity):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    description = models.CharField(max_length=200)
    languages = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Comma-separated 2-letter language codes",
    )
    content_html = models.TextField()
    content_pdf = models.TextField(help_text="Base64 encoded PDF")

    class Meta:
        db_table = "man_usermanual"
        verbose_name = "User Manual"
        verbose_name_plural = "User Manuals"

    def __str__(self):  # pragma: no cover - simple representation
        return self.title

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.slug,)


class ViewHistory(Entity):
    """Record of public site visits."""

    path = models.CharField(max_length=500)
    method = models.CharField(max_length=10)
    status_code = models.PositiveSmallIntegerField()
    status_text = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    view_name = models.CharField(max_length=200, blank=True)
    visited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-visited_at"]
        verbose_name = _("View History")
        verbose_name_plural = _("View Histories")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.method} {self.path} ({self.status_code})"


class Favorite(Entity):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favorites",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    custom_label = models.CharField(max_length=100, blank=True)
    user_data = models.BooleanField(default=False)

    class Meta:
        unique_together = ("user", "content_type")


from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=Module)
def _create_landings(
    sender, instance, created, raw, **kwargs
):  # pragma: no cover - simple handler
    if created and not raw:
        instance.create_landings()
