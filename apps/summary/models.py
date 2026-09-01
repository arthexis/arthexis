from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models


class LLMSummaryConfig(models.Model):
    """Configuration and cursor state for deterministic log summaries."""

    class OutputTarget(models.TextChoices):
        """Supported summary output targets."""

        FILE = "file", "File"

    class OutputFileFormat(models.TextChoices):
        """Supported durable file output formats."""

        TEXT = "text", "Text"
        JSON = "json", "JSON"
        BOTH = "both", "Text and JSON"

    slug = models.SlugField(unique=True, default="log-summary")
    display = models.CharField(max_length=120, default="Log Summary")
    output_target = models.CharField(
        max_length=16,
        choices=OutputTarget.choices,
        default=OutputTarget.FILE,
    )
    output_file_path = models.CharField(
        max_length=255,
        blank=True,
        help_text="Local summary file path under logs/summary. Absolute paths and traversal are rejected.",
    )
    output_file_format = models.CharField(
        max_length=16,
        choices=OutputFileFormat.choices,
        default=OutputFileFormat.TEXT,
    )
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    log_offsets = models.JSONField(default=dict, blank=True)
    last_prompt = models.TextField(blank=True)
    last_output = models.TextField(blank=True)
    last_output_file_path = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Log Summary Config"
        verbose_name_plural = "Log Summary Configs"

    def clean(self) -> None:
        super().clean()
        raw_path = str(self.output_file_path or "").strip()
        errors: dict[str, str] = {}
        if raw_path:
            path = Path(raw_path)
            if path.is_absolute() or ".." in path.parts:
                errors["output_file_path"] = (
                    "Summary output file path must stay under logs/summary "
                    "and cannot be absolute or contain '..'."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.display


__all__ = ["LLMSummaryConfig"]
