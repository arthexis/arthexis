from __future__ import annotations

import posixpath

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class DeployServer(Entity):
    """Infrastructure host that can run one or more Arthexis instances."""

    class Provider(models.TextChoices):
        AWS_LIGHTSAIL = "aws_lightsail", "AWS Lightsail"
        GENERIC = "generic", "Generic"

    name = models.CharField(
        max_length=100,
        unique=True,
        help_text=_("Human-friendly identifier used in deployment records."),
    )
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.AWS_LIGHTSAIL,
    )
    region = models.CharField(
        max_length=50,
        blank=True,
        help_text=_("Cloud region for hosted servers."),
    )
    host = models.CharField(
        max_length=255,
        help_text=_("Hostname or IP address used for remote commands."),
    )
    ssh_port = models.PositiveIntegerField(default=22)
    ssh_user = models.CharField(max_length=64, default="ubuntu")
    lightsail_instance = models.ForeignKey(
        "aws.LightsailInstance",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deploy_servers",
        help_text=_("Optional link to discovered Lightsail instance metadata."),
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        verbose_name = _("Deployment server")
        verbose_name_plural = _("Deployment servers")

    def __str__(self) -> str:  # pragma: no cover - trivial representation
        return self.name


class DeployInstance(Entity):
    """Install target for a single Arthexis instance on a deployment server."""

    service_name_validator = RegexValidator(
        regex=r"^[a-z0-9][a-z0-9._-]{1,63}$",
        message=_(
            "Use lowercase letters, numbers, dots, underscores, or hyphens "
            "(2-64 chars)."
        ),
    )

    server = models.ForeignKey(
        DeployServer,
        on_delete=models.CASCADE,
        related_name="instances",
    )
    name = models.CharField(
        max_length=100,
        help_text=_("Identifier for this Arthexis installation on the server."),
    )
    install_dir = models.CharField(
        max_length=255,
        help_text=_("Absolute directory where this instance is deployed."),
    )
    service_name = models.CharField(
        max_length=64,
        validators=[service_name_validator],
        help_text=_("System service name used to run this instance."),
    )
    env_file = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Optional absolute path to an environment file."),
    )
    branch = models.CharField(max_length=120, default="main")
    ocpp_port = models.PositiveIntegerField(
        default=9000,
        help_text=_("WebSocket/OCPP port served by this instance."),
    )
    admin_url = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Optional URL used for post-deploy health checks."),
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["server", "name"],
                name="deploy_unique_instance_name_per_server",
            ),
            models.UniqueConstraint(
                fields=["server", "install_dir"],
                name="deploy_unique_install_dir_per_server",
            ),
            models.UniqueConstraint(
                fields=["server", "service_name"],
                name="deploy_unique_service_name_per_server",
            ),
        ]
        ordering = ("server__name", "name")
        verbose_name = _("Deployment instance")
        verbose_name_plural = _("Deployment instances")

    def __str__(self) -> str:  # pragma: no cover - trivial representation
        return f"{self.server.name}:{self.name}"

    def clean(self) -> None:
        super().clean()
        self.install_dir = self._normalize_absolute_path(
            self.install_dir,
            field_name="install_dir",
        )
        if self.env_file:
            self.env_file = self._normalize_absolute_path(
                self.env_file,
                field_name="env_file",
            )

    @staticmethod
    def _normalize_absolute_path(path: str, *, field_name: str) -> str:
        candidate = (path or "").strip()
        if not candidate:
            raise ValidationError({field_name: _("This field is required.")})

        normalized = posixpath.normpath(candidate)
        if not normalized.startswith("/"):
            raise ValidationError({field_name: _("Path must be absolute.")})
        return normalized


class DeployRelease(Entity):
    """Versioned release artifact metadata used by deployment runs."""

    version = models.CharField(max_length=80, unique=True)
    git_ref = models.CharField(max_length=160)
    image = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("Deployment release")
        verbose_name_plural = _("Deployment releases")

    def __str__(self) -> str:  # pragma: no cover - trivial representation
        return self.version


class DeployRun(Entity):
    """Audit record for deploy, update, rollback, and operational actions."""

    class Action(models.TextChoices):
        DEPLOY = "deploy", "Deploy"
        MIGRATE = "migrate", "Migrate"
        RESTART = "restart", "Restart"
        ROLLBACK = "rollback", "Rollback"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    instance = models.ForeignKey(
        DeployInstance,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    release = models.ForeignKey(
        DeployRelease,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="runs",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    requested_by = models.CharField(max_length=150, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    output = models.TextField(blank=True)

    class Meta:
        ordering = ("-requested_at",)
        verbose_name = _("Deployment run")
        verbose_name_plural = _("Deployment runs")

    def __str__(self) -> str:  # pragma: no cover - trivial representation
        return f"{self.instance} [{self.action}]"
