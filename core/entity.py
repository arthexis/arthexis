import copy
import logging
import os
import re

from django.apps import apps
from django.conf import settings
from django.db import models
from django.contrib.auth.models import UserManager as DjangoUserManager

from .sigil_context import get_context

logger = logging.getLogger(__name__)


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
        super().save(*args, **kwargs)

    def resolve_sigils(self, field: str) -> str:
        """Return ``field`` value with [ROOT.KEY] tokens resolved."""
        # Find field ignoring case
        name = field.lower()
        fobj = next((f for f in self._meta.fields if f.name.lower() == name), None)
        if not fobj:
            return ""
        value = self.__dict__.get(fobj.attname, "")
        if value is None:
            return ""
        text = str(value)

        pattern = re.compile(
            r"\[([A-Za-z0-9_-]+)(?:=([A-Za-z0-9_-]+))?\.([A-Za-z0-9_-]+)(?:=([^\]]+))?\]",
            re.IGNORECASE,
        )
        SigilRoot = apps.get_model("core", "SigilRoot")

        def repl(match):
            root_name = match.group(1).replace("-", "_").upper()
            instance_id = match.group(2)
            key = match.group(3).replace("-", "_").upper()
            param = match.group(4)
            try:
                root = SigilRoot.objects.get(prefix__iexact=root_name)
                if root.context_type == SigilRoot.Context.CONFIG:
                    if root.prefix.upper() == "ENV":
                        env_val = os.environ.get(key.upper())
                        if env_val is not None:
                            return env_val
                        logger.warning(
                            "Missing environment variable for sigil [%s.%s]",
                            root_name,
                            key,
                        )
                        return match.group(0)
                    if root.prefix.upper() == "SYS":
                        if hasattr(settings, key.upper()):
                            return str(getattr(settings, key.upper()))
                        logger.warning(
                            "Missing settings attribute for sigil [%s.%s]",
                            root_name,
                            key,
                        )
                        return match.group(0)
                    if root.prefix.upper() == "CMD":
                        from django.core.management import call_command
                        from io import StringIO

                        out = StringIO()
                        try:
                            args = []
                            if param:
                                args.append(param)
                            call_command(key.lower(), *args, stdout=out)
                            return out.getvalue().strip()
                        except Exception:
                            logger.exception(
                                "Error running command for sigil [%s.%s]",
                                root_name,
                                key,
                            )
                            return match.group(0)
                elif root.context_type == SigilRoot.Context.ENTITY:
                    model = (
                        root.content_type.model_class() if root.content_type else None
                    )
                    instance = None
                    if model:
                        if instance_id:
                            instance = model.objects.filter(pk=instance_id).first()
                        elif isinstance(self, model):
                            instance = self
                        else:
                            ctx = get_context()
                            inst_pk = ctx.get(model)
                            if inst_pk is not None:
                                instance = model.objects.filter(pk=inst_pk).first()
                            if instance is None:
                                instance = model.objects.order_by("?").first()
                    if instance:
                        field = next(
                            (
                                f
                                for f in model._meta.fields
                                if f.name.lower() == key.lower()
                            ),
                            None,
                        )
                        if field:
                            val = getattr(instance, field.attname)
                            return "" if val is None else str(val)
                        logger.warning(
                            "Missing field for sigil [%s.%s]", root_name, key
                        )
                        return match.group(0)
                    logger.warning(
                        "Unresolvable sigil [%s.%s]: no instance", root_name, key
                    )
                    return match.group(0)
                logger.warning(
                    "Unresolvable sigil [%s.%s]: unsupported context", root_name, key
                )
            except SigilRoot.DoesNotExist:
                logger.warning("Unknown sigil root [%s]", root_name)
            except Exception:
                logger.exception("Error resolving sigil [%s.%s]", root_name, key)
            return match.group(0)

        return pattern.sub(repl, text)

    def delete(self, using=None, keep_parents=False):
        if self.is_seed_data:
            self.is_deleted = True
            self.save(update_fields=["is_deleted"])
        else:
            super().delete(using=using, keep_parents=keep_parents)
