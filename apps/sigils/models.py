from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models.deletion import ProtectedError
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityManager, EntityQuerySet


class SigilRootQuerySet(EntityQuerySet):
    def delete(self):  # pragma: no cover - protected from deletion
        raise ProtectedError(_("Sigil Roots cannot be deleted."), list(self))


class SigilRootManager(EntityManager.from_queryset(SigilRootQuerySet)):
    def get_by_natural_key(self, prefix: str):
        return self.get(prefix__iexact=prefix)


class SigilRoot(Entity):
    class Context(models.TextChoices):
        CONFIG = "config", "Configuration"
        ENTITY = "entity", "Entity"
        REQUEST = "request", "Request"

    prefix = models.CharField(max_length=50, unique=True)
    is_user_safe = models.BooleanField(
        default=False,
        help_text=_("Allow this sigil root in user-facing rendering contexts."),
    )
    context_type = models.CharField(max_length=20, choices=Context.choices)
    content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.CASCADE
    )

    objects = SigilRootManager()

    def save(self, *args, **kwargs):
        if self.prefix:
            self.prefix = self.prefix.upper()
        super().save(*args, **kwargs)

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

        for attr in (
            "default_instance",
            "get_default_instance",
            "default",
            "get_default",
        ):
            instance = _evaluate(getattr(model, attr, None))
            if instance:
                return instance

        manager = getattr(model, "_default_manager", None)
        if manager:
            for attr in (
                "default_instance",
                "get_default_instance",
                "default",
                "get_default",
            ):
                instance = _evaluate(getattr(manager, attr, None))
                if instance:
                    return instance

        qs = model._default_manager.all()
        ordering = list(getattr(model._meta, "ordering", []))
        if ordering:
            qs = qs.order_by(*ordering)
        else:
            qs = qs.order_by("pk")
        return qs.first()

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.prefix

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.prefix,)

    def delete(self, using=None, keep_parents=False):
        raise ProtectedError(_("Sigil Roots cannot be deleted."), [self])

    class Meta:
        db_table = "core_sigilroot"
        verbose_name = _("Sigil Root")
        verbose_name_plural = _("Sigil Roots")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(is_deleted=False),
                name="sigilroot_is_deleted_false",
            ),
        ]


class SigilRenderPolicy(models.Model):
    class UnresolvedBehavior(models.TextChoices):
        EMPTY = "empty", _("Empty output")
        PLACEHOLDER = "placeholder", _("Keep placeholder")

    singleton_key = models.CharField(default="default", editable=False, max_length=20, unique=True)
    unresolved_behavior = models.CharField(
        choices=UnresolvedBehavior.choices,
        default=UnresolvedBehavior.PLACEHOLDER,
        help_text=_(
            "Controls how unresolved/disallowed user-safe sigils render in templates."
        ),
        max_length=20,
    )

    @classmethod
    def get_solo(cls):
        policy, _ = cls.objects.get_or_create(singleton_key="default")
        return policy

    def __str__(self):  # pragma: no cover - simple representation
        return "Sigil Render Policy"

    class Meta:
        db_table = "core_sigilrenderpolicy"
        verbose_name = _("Sigil Render Policy")
        verbose_name_plural = _("Sigil Render Policy")


class CustomSigil(SigilRoot):
    class Meta:
        proxy = True
        verbose_name = _("Custom Sigil")
        verbose_name_plural = _("Custom Sigils")
