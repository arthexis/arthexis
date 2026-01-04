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
        REQUEST = "request", "Request"

    prefix = models.CharField(max_length=50, unique=True)
    context_type = models.CharField(max_length=20, choices=Context.choices)
    content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.CASCADE
    )

    objects = SigilRootManager()

    def default_instance(self):
        """Return the preferred instance for this sigil root's model.

        This allows sigils such as ``[NODE.ROLE]`` to resolve without
        specifying an explicit identifier by letting the related model (or
        its manager) advertise a default object.
        """

        model = self.content_type.model_class() if self.content_type else None
        if model is None:
            return None

        def _evaluate(source):
            if source is None:
                return None
            try:
                candidate = source() if callable(source) else source
            except TypeError:
                return None
            if isinstance(candidate, models.Model):
                return candidate
            return None

        for attr in ("default_instance", "get_default_instance", "default", "get_default"):
            instance = _evaluate(getattr(model, attr, None))
            if instance:
                return instance

        manager = getattr(model, "_default_manager", None)
        if manager:
            for attr in ("default_instance", "get_default_instance", "default", "get_default"):
                instance = _evaluate(getattr(manager, attr, None))
                if instance:
                    return instance

        qs = model._default_manager.all()
        ordering = list(getattr(model._meta, "ordering", []))
        if ordering:
            qs = qs.order_by(*ordering)
        else:
            qs = qs.order_by("?")
        return qs.first()

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
