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
    node_roles = models.ManyToManyField(
        "nodes.NodeRole", blank=True, related_name="agent_skills"
    )

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


class AgentSkillFile(models.Model):
    """One file captured from a multi-file Codex skill package."""

    class Portability(models.TextChoices):
        PORTABLE = "portable", "Portable"
        OPERATOR_SCOPED = "operator_scoped", "Operator scoped"
        DEVICE_SCOPED = "device_scoped", "Device scoped"
        SECRET = "secret", "Secret"
        CACHE = "cache", "Cache"
        STATE = "state", "State"
        GENERATED_REFERENCE = "generated_reference", "Generated reference"

    skill = models.ForeignKey(
        AgentSkill,
        on_delete=models.CASCADE,
        related_name="package_files",
    )
    relative_path = models.CharField(max_length=500)
    content = models.TextField(blank=True)
    content_sha256 = models.CharField(max_length=64, blank=True)
    portability = models.CharField(
        max_length=32,
        choices=Portability.choices,
        default=Portability.PORTABLE,
    )
    included_by_default = models.BooleanField(default=True)
    exclusion_reason = models.CharField(max_length=255, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("skill__slug", "relative_path")
        unique_together = (("skill", "relative_path"),)
        verbose_name = "Agent Skill File"
        verbose_name_plural = "Agent Skill Files"

    def __str__(self) -> str:
        return f"{self.skill.slug}:{self.relative_path}"
