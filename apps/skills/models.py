from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class Skill(Entity):
    """Portable operator knowledge and routing for Codex-compatible tools."""

    slug = models.SlugField(max_length=100, unique=True)
    title = models.CharField(max_length=150)
    description = models.CharField(
        max_length=720,
        blank=True,
        help_text=_("Compact index text for matching, RFID cards, and remote lookup."),
    )
    markdown = models.TextField()
    node_roles = models.ManyToManyField(
        "nodes.NodeRole",
        blank=True,
        related_name="skills",
        help_text=_("Optional node roles where this skill is especially relevant."),
    )

    class Meta:
        ordering = ("slug",)
        verbose_name = "Skill"
        verbose_name_plural = "Skills"

    def __str__(self) -> str:
        return self.slug

    @property
    def skill_path(self) -> Path:
        return Path(settings.BASE_DIR) / "skills" / self.slug / "SKILL.md"

    def clean(self) -> None:
        super().clean()
        if not self.slug:
            raise ValidationError({"slug": "Slug is required."})
        if self.description and len(self.description) > 720:
            raise ValidationError(
                {"description": "Skill descriptions must fit within 720 characters."}
            )


class SkillFile(models.Model):
    """One file captured from a multi-file portable skill package."""

    class Portability(models.TextChoices):
        PORTABLE = "portable", "Portable"
        OPERATOR_SCOPED = "operator_scoped", "Operator scoped"
        DEVICE_SCOPED = "device_scoped", "Device scoped"
        SECRET = "secret", "Secret"
        CACHE = "cache", "Cache"
        STATE = "state", "State"
        GENERATED_REFERENCE = "generated_reference", "Generated reference"

    skill = models.ForeignKey(
        Skill,
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
        verbose_name = "Skill File"
        verbose_name_plural = "Skill Files"

    def __str__(self) -> str:
        return f"{self.skill.slug}:{self.relative_path}"


class Agent(Entity):
    """AGENTS.md context block selected by node role and enabled features."""

    slug = models.SlugField(max_length=100, unique=True)
    title = models.CharField(max_length=150)
    description = models.CharField(
        max_length=720,
        blank=True,
        help_text=_("Compact summary for search, cards, and package previews."),
    )
    instructions = models.TextField(
        blank=True,
        help_text=_("Markdown context rendered into the local dynamic AGENTS file."),
    )
    priority = models.PositiveSmallIntegerField(
        default=100,
        help_text=_("Lower values render earlier within the same context tier."),
    )
    is_default = models.BooleanField(
        default=False,
        help_text=_("Render for every node when enabled."),
    )
    node_roles = models.ManyToManyField(
        "nodes.NodeRole",
        blank=True,
        related_name="agents",
        help_text=_(
            "Node role context. Role-matched rules render with highest priority."
        ),
    )
    node_features = models.ManyToManyField(
        "nodes.NodeFeature",
        blank=True,
        related_name="agents",
        help_text=_("Render when the local node has one of these node features."),
    )
    suite_features = models.ManyToManyField(
        "features.Feature",
        blank=True,
        related_name="agents",
        help_text=_("Render when one of these suite features is enabled for the node."),
    )

    class Meta:
        ordering = ("priority", "slug")
        verbose_name = "Agent"
        verbose_name_plural = "Agents"

    def __str__(self) -> str:
        return self.slug


class Hook(Entity):
    """Deterministic command hook for Codex-compatible operator sessions."""

    class Event(models.TextChoices):
        SESSION_START = "session_start", "Session start"
        BEFORE_PROMPT = "before_prompt", "Before prompt"
        AFTER_RESPONSE = "after_response", "After response"
        BEFORE_COMMAND = "before_command", "Before command"
        AFTER_COMMAND = "after_command", "After command"

    class Platform(models.TextChoices):
        ANY = "any", "Any"
        LINUX = "linux", "Linux"
        WINDOWS = "windows", "Windows"

    slug = models.SlugField(max_length=100, unique=True)
    title = models.CharField(max_length=150)
    description = models.CharField(
        max_length=720,
        blank=True,
        help_text=_("Compact summary for search, cards, and package previews."),
    )
    event = models.CharField(
        max_length=32,
        choices=Event.choices,
        default=Event.SESSION_START,
    )
    platform = models.CharField(
        max_length=16,
        choices=Platform.choices,
        default=Platform.ANY,
    )
    command = models.TextField(
        help_text=_(
            "Deterministic command to run. Use portable suite paths and SIGILS instead "
            "of operator-local paths."
        ),
    )
    working_directory = models.CharField(max_length=500, blank=True)
    environment = models.JSONField(default=dict, blank=True)
    timeout_seconds = models.PositiveSmallIntegerField(default=60)
    enabled = models.BooleanField(default=True)
    priority = models.PositiveSmallIntegerField(default=100)
    node_roles = models.ManyToManyField(
        "nodes.NodeRole",
        blank=True,
        related_name="hooks",
    )
    node_features = models.ManyToManyField(
        "nodes.NodeFeature",
        blank=True,
        related_name="hooks",
    )
    suite_features = models.ManyToManyField(
        "features.Feature",
        blank=True,
        related_name="hooks",
    )

    class Meta:
        ordering = ("event", "priority", "slug")
        verbose_name = "Hook"
        verbose_name_plural = "Hooks"

    def __str__(self) -> str:
        return self.slug
