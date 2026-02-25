"""Models for configuring API entry points and resource methods."""

from django.core.exceptions import ValidationError
from django.db import models


class APIExplorer(models.Model):
    """Represents a configurable API entry point."""

    name = models.CharField(max_length=120, unique=True)
    base_url = models.URLField(
        max_length=500,
        help_text="Base URL for this API, such as https://api.example.com/v1.",
    )
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "API Explorer"
        verbose_name_plural = "API Explorers"

    def __str__(self) -> str:
        """Return a readable label for this API entry point."""

        return self.name


class ResourceMethod(models.Model):
    """Defines a resource+method operation for an API explorer entry point."""

    class HttpMethod(models.TextChoices):
        """Supported HTTP methods for resource method operations."""

        GET = "GET", "GET"
        POST = "POST", "POST"
        PUT = "PUT", "PUT"
        PATCH = "PATCH", "PATCH"
        DELETE = "DELETE", "DELETE"

    api = models.ForeignKey(APIExplorer, on_delete=models.CASCADE, related_name="resource_methods")
    operation_name = models.CharField(max_length=150)
    resource_path = models.CharField(max_length=255, help_text="Relative path, e.g. /users/{id}.")
    http_method = models.CharField(max_length=10, choices=HttpMethod.choices)
    request_structure = models.JSONField(default=dict, blank=True)
    response_structure = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("api__name", "resource_path", "http_method", "operation_name")
        verbose_name = "Resource Method"
        verbose_name_plural = "Resource Methods"
        constraints = [
            models.UniqueConstraint(
                fields=("api", "resource_path", "http_method", "operation_name"),
                name="apis_resource_method_unique_operation",
            )
        ]

    def __str__(self) -> str:
        """Return a readable resource method label."""

        return f"{self.api.name}: {self.http_method} {self.resource_path} ({self.operation_name})"

    def clean(self) -> None:
        """Validate resource path and request/response structures."""

        super().clean()
        if not self.resource_path.startswith("/"):
            raise ValidationError({"resource_path": "Resource path must start with '/'."})

        for field_name in ("request_structure", "response_structure"):
            payload = getattr(self, field_name)
            if payload in (None, ""):
                continue
            if not isinstance(payload, (dict, list)):
                raise ValidationError({field_name: "Structure must be a JSON object or array."})
