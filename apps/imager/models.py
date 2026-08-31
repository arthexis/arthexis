"""Domain models for Raspberry Pi image artifact and burn workflows."""

import uuid

from django.db import models


class RaspberryPiImageArtifact(models.Model):
    """Persist metadata for generated Raspberry Pi image artifacts."""

    name = models.CharField(max_length=120, unique=True)
    target = models.CharField(max_length=40, default="rpi-4b")
    base_image_uri = models.CharField(max_length=500)
    output_filename = models.CharField(max_length=255)
    output_path = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.BigIntegerField()
    download_uri = models.URLField(blank=True, default="")
    build_engine = models.CharField(max_length=80, default="arthexis-bootstrap")
    build_profile = models.CharField(max_length=80, default="bootstrap")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Raspberry Pi image artifact"
        verbose_name_plural = "Raspberry Pi image artifacts"

    def __str__(self) -> str:
        """Return a readable artifact name."""

        return f"{self.name} ({self.target})"


class RaspberryPiImageBurnJob(models.Model):
    """Persist a queued SD-card burn so service workers can survive restarts."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    artifact = models.ForeignKey(
        RaspberryPiImageArtifact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="burn_jobs",
    )
    artifact_name = models.CharField(max_length=120, blank=True, default="")
    image_path = models.CharField(max_length=500, blank=True, default="")
    image_sha256 = models.CharField(max_length=64, blank=True, default="")
    image_size_bytes = models.BigIntegerField(default=0)
    device_path = models.CharField(max_length=255)
    device_identity = models.JSONField(default=dict, blank=True)
    backup = models.BooleanField(default=False)
    backup_dir = models.CharField(max_length=500, blank=True, default="")
    progress_bytes = models.BigIntegerField(default=0)
    progress_total_bytes = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    attempts = models.PositiveIntegerField(default=0)
    log = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Raspberry Pi image burn job"
        verbose_name_plural = "Raspberry Pi image burn jobs"
        constraints = [
            models.CheckConstraint(
                condition=(
                    (~models.Q(artifact_name="") & models.Q(image_path=""))
                    | (models.Q(artifact_name="") & ~models.Q(image_path=""))
                ),
                name="imager_burn_job_exactly_one_source",
            ),
        ]
        indexes = [
            models.Index(fields=("status", "created_at")),
        ]

    def __str__(self) -> str:
        """Return a compact job identifier for admin and command output."""

        source = self.artifact_name or self.image_path
        return f"{self.uuid} {self.status} {source} -> {self.device_path}"

    @property
    def progress_percent(self) -> int | None:
        """Return a whole-number burn progress percentage when size is known."""

        if self.status == self.Status.SUCCEEDED:
            return 100
        total_bytes = self.progress_total_bytes or self.image_size_bytes
        if total_bytes <= 0:
            return None
        progress_bytes = min(max(self.progress_bytes, 0), total_bytes)
        return int(progress_bytes * 100 / total_bytes)
