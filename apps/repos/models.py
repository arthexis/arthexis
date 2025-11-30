from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityManager


class RepositoryManager(EntityManager):
    def get_by_natural_key(self, owner: str, name: str):
        return self.get(owner=owner, name=name)


class Repository(Entity):
    """Source code repository reference."""

    objects = RepositoryManager()

    owner = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_private = models.BooleanField(default=False)
    html_url = models.URLField(blank=True)
    api_url = models.URLField(blank=True)
    ssh_url = models.CharField(max_length=255, blank=True)
    default_branch = models.CharField(max_length=100, blank=True)

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.owner, self.name)

    @property
    def slug(self):  # pragma: no cover - simple representation
        return f"{self.owner}/{self.name}".strip("/")

    def __str__(self):  # pragma: no cover - simple representation
        return self.slug

    class Meta:
        verbose_name = _("Repository")
        verbose_name_plural = _("Repositories")
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"], name="unique_repository_owner_name"
            )
        ]
