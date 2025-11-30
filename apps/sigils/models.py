from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityManager


class SigilRootManager(EntityManager):
    def get_by_natural_key(self, prefix: str):
        return self.get(prefix=prefix)


class SigilRoot(Entity):
    class Context(models.TextChoices):
        CONFIG = "config", "Configuration"
        ENTITY = "entity", "Entity"

    prefix = models.CharField(max_length=50, unique=True)
    context_type = models.CharField(max_length=20, choices=Context.choices)
    content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.CASCADE
    )

    objects = SigilRootManager()

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.prefix

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.prefix,)

    class Meta:
        db_table = "core_sigilroot"
        verbose_name = _("Sigil Root")
        verbose_name_plural = _("Sigil Roots")


class CustomSigil(SigilRoot):
    class Meta:
        proxy = True
        verbose_name = _("Custom Sigil")
        verbose_name_plural = _("Custom Sigils")
