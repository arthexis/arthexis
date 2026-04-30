from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.base.models import Entity


class AgentSkill(Entity):
    """Skill markdown content scoped to optional node roles."""

    slug = models.SlugField(max_length=100, unique=True)
    title = models.CharField(max_length=150)
    markdown = models.TextField()
    node_roles = models.ManyToManyField("nodes.NodeRole", blank=True, related_name="agent_skills")

    class Meta:
        ordering = ("slug",)
        verbose_name = "Agent Skill"
        verbose_name_plural = "Agent Skills"

    def __str__(self) -> str:
        return self.slug

    @property
    def skill_path(self) -> Path:
        return Path(settings.BASE_DIR) / "skills" / self.slug / "SKILL.md"

    def clean(self) -> None:
        super().clean()
        if not self.slug:
            raise ValidationError({"slug": "Slug is required."})
