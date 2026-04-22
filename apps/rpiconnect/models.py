"""Domain models for Raspberry Pi Connect integration management."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction


class ConnectAccount(models.Model):
    """Stores account-level metadata and token references for Connect APIs."""

    class AccountType(models.TextChoices):
        """Supported account ownership types."""

        ORG = "org", "Organization"
        PERSONAL = "personal", "Personal"

    name = models.CharField(max_length=120)
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    organization_name = models.CharField(max_length=120, blank=True)
    owner_name = models.CharField(max_length=120, blank=True)
    owner_email = models.EmailField(blank=True)
    token_reference = models.CharField(max_length=255)
    refresh_token_reference = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        """Return a readable account label."""

        return self.name


class ConnectDevice(models.Model):
    """Represents a Raspberry Pi Connect-managed device."""

    class EnrollmentSource(models.TextChoices):
        """How this device entered Arthexis tracking."""

        API_SYNC = "api_sync", "API Sync"
        MANUAL = "manual", "Manual"
        IMPORT = "import", "Import"

    account = models.ForeignKey(
        ConnectAccount,
        on_delete=models.CASCADE,
        related_name="devices",
    )
    device_id = models.CharField(max_length=120, unique=True)
    hardware_model = models.CharField(max_length=120)
    os_release = models.CharField(max_length=120, blank=True)
    is_online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)
    enrollment_source = models.CharField(
        max_length=20,
        choices=EnrollmentSource.choices,
        default=EnrollmentSource.API_SYNC,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("device_id",)

    def __str__(self) -> str:
        """Return the stable external device identifier."""

        return self.device_id


class ConnectImageRelease(models.Model):
    """Tracks release artifacts that can be deployed to devices."""

    name = models.CharField(max_length=120)
    version = models.CharField(max_length=40)
    build_metadata = models.JSONField(default=dict, blank=True)
    artifact_url = models.URLField(max_length=500)
    checksum = models.CharField(max_length=128)
    compatibility_tags = models.JSONField(default=list, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-released_at", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("name", "version"),
                name="rpiconnect_release_unique_name_version",
            )
        ]

    def __str__(self) -> str:
        """Return a readable release label."""

        return f"{self.name} ({self.version})"


class ConnectUpdateCampaign(models.Model):
    """Defines a coordinated update rollout against a target device set."""

    class Strategy(models.TextChoices):
        """Campaign rollout strategies."""

        ALL_AT_ONCE = "all_at_once", "All at once"
        BATCHED = "batched", "Batched"
        CANARY = "canary", "Canary"

    class Status(models.TextChoices):
        """Campaign lifecycle status."""

        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"
        STOPPED = "stopped", "Stopped"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    release = models.ForeignKey(
        ConnectImageRelease,
        on_delete=models.PROTECT,
        related_name="campaigns",
    )
    target_set = models.JSONField(default=dict)
    strategy = models.CharField(max_length=20, choices=Strategy.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="connect_update_campaigns",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        """Return a readable campaign label."""

        return f"Campaign {self.pk}"


class ConnectUpdateDeployment(models.Model):
    """Per-device deployment status and timestamps within a campaign."""

    class Status(models.TextChoices):
        """Deployment lifecycle status."""

        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        ROLLED_BACK = "rolled_back", "Rolled back"

    class FailureClassification(models.TextChoices):
        """Normalized failure buckets used for retries and troubleshooting."""

        NONE = "", "Unknown/Not Set"
        NETWORK = "network", "Network"
        ARTIFACT_FETCH = "artifact_fetch", "Artifact Fetch"
        VERIFICATION = "verification", "Verification"
        APPLY_REBOOT = "apply_reboot", "Apply/Reboot"

    ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
        Status.PENDING: {Status.IN_PROGRESS, Status.FAILED, Status.ROLLED_BACK},
        Status.IN_PROGRESS: {Status.SUCCEEDED, Status.FAILED, Status.ROLLED_BACK},
        Status.SUCCEEDED: set(),
        Status.FAILED: {Status.ROLLED_BACK},
        Status.ROLLED_BACK: set(),
    }

    campaign = models.ForeignKey(
        ConnectUpdateCampaign,
        on_delete=models.CASCADE,
        related_name="deployments",
    )
    device = models.ForeignKey(
        ConnectDevice,
        on_delete=models.CASCADE,
        related_name="update_deployments",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_payload = models.JSONField(default=dict, blank=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failure_classification = models.CharField(
        max_length=20,
        choices=FailureClassification.choices,
        blank=True,
        default=FailureClassification.NONE,
    )
    retry_attempts = models.PositiveSmallIntegerField(default=0)
    retry_max_attempts = models.PositiveSmallIntegerField(default=3)
    retry_cooldown_seconds = models.PositiveIntegerField(default=300)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "device"),
                name="rpiconnect_deployment_unique_campaign_device",
            )
        ]

    def __str__(self) -> str:
        """Return a readable deployment label."""

        return f"Campaign {self.campaign_id} / {self.device.device_id}"

    def clean(self) -> None:
        """Enforce monotonic deployment status transitions."""

        super().clean()
        if not self.pk:
            return

        original_status = (
            type(self)
            .objects.filter(pk=self.pk)
            .values_list("status", flat=True)
            .first()
        )
        if original_status is None or original_status == self.status:
            return

        allowed_statuses = self.ALLOWED_STATUS_TRANSITIONS.get(original_status, set())
        if self.status not in allowed_statuses:
            raise ValidationError(
                {
                    "status": (
                        f"Invalid deployment status transition: {original_status} -> {self.status}."
                    )
                },
                code="invalid_status_transition",
            )

    def save(self, *args, **kwargs):
        """Persist only validated deployment state transitions."""

        update_fields = kwargs.get("update_fields")
        update_field_names = set(update_fields) if update_fields is not None else None
        status_will_be_saved = update_field_names is None or "status" in update_field_names
        original_status = None
        if self.pk and status_will_be_saved:
            original_status = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
        self.full_clean()
        super().save(*args, **kwargs)
        terminal_statuses = {
            self.Status.SUCCEEDED,
            self.Status.FAILED,
            self.Status.ROLLED_BACK,
        }
        persisted_status = self.status
        if status_will_be_saved:
            persisted_status = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
        if (
            status_will_be_saved
            and original_status != persisted_status
            and persisted_status in terminal_statuses
        ):
            event_type = f"deployment.{persisted_status}"
            ConnectCampaignEvent.objects.create(
                campaign=self.campaign,
                deployment=self,
                event_type=event_type,
                from_status=original_status or "",
                to_status=persisted_status,
            )
            from apps.rpiconnect.services import CampaignService

            campaign_id = self.campaign_id
            transaction.on_commit(
                lambda campaign_id=campaign_id: CampaignService().sync_rollout_progress(
                    campaign_id=campaign_id
                )
            )


class ConnectCampaignEvent(models.Model):
    """Durable event log for campaign and deployment state transitions."""

    campaign = models.ForeignKey(
        ConnectUpdateCampaign,
        on_delete=models.SET_NULL,
        related_name="events",
        null=True,
        blank=True,
    )
    deployment = models.ForeignKey(
        ConnectUpdateDeployment,
        on_delete=models.SET_NULL,
        related_name="events",
        null=True,
        blank=True,
    )
    event_type = models.CharField(max_length=80)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="connect_campaign_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self) -> str:
        """Return a readable event label."""

        return f"Campaign {self.campaign_id}: {self.event_type}"


class ConnectIngestionEvent(models.Model):
    """Normalized + raw snippets for authenticated update event ingestion."""

    class EventType(models.TextChoices):
        """Supported external update/deployment event families."""

        UPDATE = "update", "Update"
        DEPLOYMENT = "deployment", "Deployment"

    event_id = models.CharField(max_length=120)
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    external_device_id = models.CharField(max_length=120)
    device = models.ForeignKey(
        ConnectDevice,
        on_delete=models.SET_NULL,
        related_name="ingestion_events",
        null=True,
        blank=True,
    )
    deployment = models.ForeignKey(
        ConnectUpdateDeployment,
        on_delete=models.SET_NULL,
        related_name="ingestion_events",
        null=True,
        blank=True,
    )
    campaign = models.ForeignKey(
        ConnectUpdateCampaign,
        on_delete=models.SET_NULL,
        related_name="ingestion_events",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=30, blank=True)
    failure_classification = models.CharField(
        max_length=20,
        choices=ConnectUpdateDeployment.FailureClassification.choices,
        blank=True,
        default=ConnectUpdateDeployment.FailureClassification.NONE,
    )
    retry_attempt = models.PositiveSmallIntegerField(default=0)
    cooldown_until = models.DateTimeField(null=True, blank=True)
    payload_snippet = models.JSONField(default=dict, blank=True)
    normalized_payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("event_id", "external_device_id"),
                name="rpiconnect_ingestion_unique_event_device",
            )
        ]

    def __str__(self) -> str:
        """Return a readable event label."""

        return f"{self.external_device_id} / {self.event_id}"
