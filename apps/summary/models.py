from __future__ import annotations

from django.db import models
from django.utils import timezone


class LLMSummaryConfig(models.Model):
    """Configuration and cursor state for LCD log summaries."""

    class Backend(models.TextChoices):
        """Supported in-process summarizer backends."""

        LLAMA_CPP_SERVER = "llama_cpp_server", "llama.cpp OpenAI-compatible server"
        DETERMINISTIC = "deterministic", "Deterministic built-in summarizer"

    slug = models.SlugField(unique=True, default="lcd-log-summary")
    display = models.CharField(max_length=120, default="LCD Log Summary")
    selected_model = models.CharField(max_length=120, blank=True)
    model_path = models.CharField(max_length=255, blank=True)
    backend = models.CharField(
        max_length=32,
        choices=Backend.choices,
        default=Backend.LLAMA_CPP_SERVER,
    )
    runtime_base_url = models.CharField(
        max_length=255,
        blank=True,
        default="http://127.0.0.1:8080/v1",
    )
    runtime_binary_path = models.CharField(
        max_length=255,
        blank=True,
        default="llama-server",
    )
    runtime_model_id = models.CharField(max_length=255, blank=True)
    runtime_is_ready = models.BooleanField(default=False)
    last_runtime_check_at = models.DateTimeField(null=True, blank=True)
    last_runtime_error = models.TextField(blank=True)
    model_command_audit = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    installed_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    log_offsets = models.JSONField(default=dict, blank=True)
    last_prompt = models.TextField(blank=True)
    last_output = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "LLM Summary Config"
        verbose_name_plural = "LLM Summary Configs"

    def mark_installed(self) -> None:
        """Record the first successful local model directory installation time."""

        if self.installed_at is None:
            self.installed_at = timezone.now()

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.display


__all__ = ["LLMSummaryConfig"]
