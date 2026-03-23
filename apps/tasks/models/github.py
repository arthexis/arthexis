from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class GitHubIssueTemplate(Entity):
    """Reusable sigil-enabled templates used to create GitHub issues."""

    name = models.CharField(
        _("Name"),
        max_length=200,
        unique=True,
        help_text=_("Internal name shown when selecting a template."),
    )
    title_template = models.CharField(
        _("Title template"),
        max_length=240,
        help_text=_("Issue title supporting sigils such as [TASK.DESCRIPTION]."),
    )
    body_template = models.TextField(
        _("Body template"),
        help_text=_("Issue body supporting sigils and multiline content."),
    )
    labels = models.CharField(
        _("Labels"),
        max_length=500,
        blank=True,
        help_text=_("Comma-separated GitHub labels to apply to created issues."),
    )

    class Meta:
        verbose_name = _("GitHub Issue Template")
        verbose_name_plural = _("GitHub Issue Templates")
        ordering = ("name",)
        db_table = "core_githubissuetemplate"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def resolve_labels(self) -> list[str]:
        """Return normalized labels from the comma-separated ``labels`` field."""

        return [label.strip() for label in self.labels.split(",") if label.strip()]
