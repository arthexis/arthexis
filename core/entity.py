import copy
import logging

from django.contrib.auth.models import UserManager as DjangoUserManager
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.dispatch import Signal

logger = logging.getLogger(__name__)


user_data_flag_updated = Signal()


class EntityQuerySet(models.QuerySet):
    def delete(self):  # pragma: no cover - delegates to instance delete
        deleted = 0
        for obj in self:
            obj.delete()
            deleted += 1
        return deleted, {}

    def update(self, **kwargs):
        invalidate_user_data_cache = "is_user_data" in kwargs
        updated = super().update(**kwargs)
        if invalidate_user_data_cache and updated:
            user_data_flag_updated.send(sender=self.model)
        return updated


class EntityManager(models.Manager):
    def get_queryset(self):
        return EntityQuerySet(self.model, using=self._db).filter(is_deleted=False)


class EntityAllManager(models.Manager):
    def get_queryset(self):
        return EntityQuerySet(self.model, using=self._db)


class EntityUserManager(DjangoUserManager):
    def get_queryset(self):
        return EntityQuerySet(self.model, using=self._db).filter(is_deleted=False)


class Entity(models.Model):
    """Base model providing seed data tracking and soft deletion."""

    is_seed_data = models.BooleanField(default=False, editable=False)
    is_user_data = models.BooleanField(default=False, editable=False)
    is_deleted = models.BooleanField(default=False, editable=False)

    objects = EntityManager()
    all_objects = EntityAllManager()

    class Meta:
        abstract = True

    def clone(self):
        """Return an unsaved copy of this instance."""
        new = copy.copy(self)
        new.pk = None
        return new

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = type(self).all_objects.get(pk=self.pk)
            except type(self).DoesNotExist:
                pass
            else:
                self.is_seed_data = old.is_seed_data
                self.is_user_data = old.is_user_data
        super().save(*args, **kwargs)

    @classmethod
    def _unique_field_groups(cls):
        """Return concrete field tuples enforcing uniqueness for this model."""

        opts = cls._meta
        groups: list[tuple[models.Field, ...]] = []

        for field in opts.concrete_fields:
            if field.unique and not field.primary_key:
                groups.append((field,))

        for unique in opts.unique_together:
            fields: list[models.Field] = []
            for name in unique:
                try:
                    field = opts.get_field(name)
                except FieldDoesNotExist:
                    fields = []
                    break
                if not getattr(field, "concrete", False) or field.primary_key:
                    fields = []
                    break
                fields.append(field)
            if fields:
                groups.append(tuple(fields))

        for constraint in opts.constraints:
            if not isinstance(constraint, models.UniqueConstraint):
                continue
            if not constraint.fields or constraint.condition is not None:
                continue
            fields = []
            for name in constraint.fields:
                try:
                    field = opts.get_field(name)
                except FieldDoesNotExist:
                    fields = []
                    break
                if not getattr(field, "concrete", False) or field.primary_key:
                    fields = []
                    break
                fields.append(field)
            if fields:
                groups.append(tuple(fields))

        unique_groups: list[tuple[models.Field, ...]] = []
        seen: set[tuple[str, ...]] = set()
        for fields in groups:
            key = tuple(field.attname for field in fields)
            if key in seen:
                continue
            seen.add(key)
            unique_groups.append(fields)
        return unique_groups

    def resolve_sigils(self, field: str) -> str:
        """Return ``field`` value with [ROOT.KEY] tokens resolved."""
        name = field.lower()
        fobj = next((f for f in self._meta.fields if f.name.lower() == name), None)
        if not fobj:
            return ""
        value = self.__dict__.get(fobj.attname, "")
        if value is None:
            return ""
        from .sigil_resolver import resolve_sigils as _resolve

        return _resolve(str(value), current=self)

    def delete(self, using=None, keep_parents=False):
        if self.is_seed_data:
            self.is_deleted = True
            self.save(update_fields=["is_deleted"])
        else:
            super().delete(using=using, keep_parents=keep_parents)
