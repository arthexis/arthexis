from __future__ import annotations

from django.db import models
from django.db.utils import IntegrityError

from apps.base.models import Entity

from .utils import NameRepresentationMixin


class NodeRoleManager(models.Manager):
    """Manager for NodeRole models."""

    def get_by_natural_key(self, name: str):
        """Return a role by its natural key name."""
        return self.get(name=name)

    def create(self, **kwargs):
        """Create or update a node role while preserving uniqueness."""
        name = kwargs.get("name")
        if name:
            existing = self.filter(name=name).first()
            if existing:
                update_fields = []
                for field, value in kwargs.items():
                    if field == "name":
                        continue
                    if value is not None and getattr(existing, field, None) != value:
                        setattr(existing, field, value)
                        update_fields.append(field)

                if update_fields:
                    existing.save(update_fields=update_fields)

                return existing

        try:
            return super().create(**kwargs)
        except IntegrityError:
            if name:
                existing = self.filter(name=name).first()
                if existing:
                    return existing
            raise


class NodeRole(NameRepresentationMixin, Entity):
    """Assignable role for a :class:`Node`."""

    name = models.CharField(max_length=50, unique=True)
    acronym = models.CharField(max_length=4, unique=True, null=True, blank=True)
    description = models.CharField(max_length=200, blank=True)
    default_upgrade_policy = models.ForeignKey(
        "nodes.UpgradePolicy",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_roles",
        help_text="Upgrade policy assigned by default to nodes with this role.",
    )

    objects = NodeRoleManager()

    class Meta:
        ordering = ["name"]
        verbose_name = "Node Role"
        verbose_name_plural = "Node Roles"

    def natural_key(self):  # pragma: no cover - simple representation
        """Return the natural key for serialization."""
        return (self.name,)


def get_terminal_role():
    """Return the NodeRole representing a Terminal if it exists."""
    return NodeRole.objects.filter(name="Terminal").first()
