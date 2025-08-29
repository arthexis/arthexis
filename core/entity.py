from django.db import models
from django.contrib.auth.models import UserManager as DjangoUserManager


class EntityQuerySet(models.QuerySet):
    def delete(self):  # pragma: no cover - delegates to instance delete
        deleted = 0
        for obj in self:
            obj.delete()
            deleted += 1
        return deleted, {}


class EntityManager(models.Manager):
    def get_queryset(self):
        return EntityQuerySet(self.model, using=self._db).filter(is_deleted=False)


class EntityUserManager(DjangoUserManager):
    def get_queryset(self):
        return EntityQuerySet(self.model, using=self._db).filter(is_deleted=False)


class Entity(models.Model):
    """Base model providing seed data tracking and soft deletion."""

    is_seed_data = models.BooleanField(default=False, editable=False)
    is_deleted = models.BooleanField(default=False, editable=False)

    objects = EntityManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = type(self).all_objects.get(pk=self.pk)
            except type(self).DoesNotExist:
                pass
            else:
                self.is_seed_data = old.is_seed_data
        super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        if self.is_seed_data:
            self.is_deleted = True
            self.save(update_fields=["is_deleted"])
        else:
            super().delete(using=using, keep_parents=keep_parents)
