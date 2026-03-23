"""Models for project bundle headers and linked objects."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.base.models import Entity


class Project(Entity):
    """Header information for a working project bundle."""

    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        """Return the display label for admin list pages."""

        return self.name


class ProjectItem(models.Model):
    """Map a project to an arbitrary object via content types."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="items",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64)
    content_object = GenericForeignKey("content_type", "object_id")
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("project", "content_type__app_label", "content_type__model", "object_id")
        constraints = [
            models.UniqueConstraint(
                fields=("project", "content_type", "object_id"),
                name="projects_unique_project_item",
            )
        ]

    def __str__(self) -> str:
        """Return a compact display label for the linked object."""

        return f"{self.project}: {self.content_type.app_label}.{self.content_type.model}#{self.object_id}"

    @classmethod
    def get_bundle_model_classes(cls) -> list[type[Entity]]:
        """Return concrete entity models that can be bundled."""

        models_by_label: dict[str, type[Entity]] = {}
        for model, model_admin in admin.site._registry.items():
            if not issubclass(model, Entity):
                continue
            if not hasattr(model_admin, "add_selected_to_project"):
                continue
            concrete_model = model._meta.concrete_model
            models_by_label[concrete_model._meta.label_lower] = concrete_model
        return list(models_by_label.values())
