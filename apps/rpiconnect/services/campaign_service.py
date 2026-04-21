"""Campaign orchestration services for Raspberry Pi Connect rollouts."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import connection
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.rpiconnect.models import (
    ConnectCampaignEvent,
    ConnectDevice,
    ConnectImageRelease,
    ConnectUpdateCampaign,
    ConnectUpdateDeployment,
)


class CampaignServiceError(ValidationError):
    """Raised when campaign orchestration rules are violated."""


@dataclass(frozen=True)
class RolloutStage:
    """Represents a staged rollout subset of devices."""

    label: str
    device_ids: list[int]


class CampaignService:
    """Handles campaign creation, rollout policies, and status transitions."""

    EVENT_CAMPAIGN_CREATED = "campaign.created"

    TERMINAL_CAMPAIGN_STATUSES = {
        ConnectUpdateCampaign.Status.CANCELLED,
        ConnectUpdateCampaign.Status.COMPLETED,
        ConnectUpdateCampaign.Status.FAILED,
        ConnectUpdateCampaign.Status.STOPPED,
    }

    ACTIVE_CAMPAIGN_STATUSES = {
        ConnectUpdateCampaign.Status.DRAFT,
        ConnectUpdateCampaign.Status.RUNNING,
        ConnectUpdateCampaign.Status.PAUSED,
    }

    CAMPAIGN_TRANSITIONS = {
        ConnectUpdateCampaign.Status.DRAFT: {
            ConnectUpdateCampaign.Status.RUNNING,
            ConnectUpdateCampaign.Status.CANCELLED,
        },
        ConnectUpdateCampaign.Status.RUNNING: {
            ConnectUpdateCampaign.Status.PAUSED,
            ConnectUpdateCampaign.Status.STOPPED,
            ConnectUpdateCampaign.Status.CANCELLED,
            ConnectUpdateCampaign.Status.COMPLETED,
            ConnectUpdateCampaign.Status.FAILED,
        },
        ConnectUpdateCampaign.Status.PAUSED: {
            ConnectUpdateCampaign.Status.RUNNING,
            ConnectUpdateCampaign.Status.STOPPED,
            ConnectUpdateCampaign.Status.CANCELLED,
        },
        ConnectUpdateCampaign.Status.STOPPED: set(),
        ConnectUpdateCampaign.Status.CANCELLED: set(),
        ConnectUpdateCampaign.Status.COMPLETED: set(),
        ConnectUpdateCampaign.Status.FAILED: set(),
    }

    def create_campaign(
        self,
        *,
        release: ConnectImageRelease,
        target_set: dict,
        strategy: str,
        created_by=None,
        notes: str = "",
        override_conflicts: bool = False,
        batch_size: int = 0,
        canary_size: int = 1,
    ) -> ConnectUpdateCampaign:
        """Create campaign, resolve targets, and schedule deployments."""

        self._validate_release(release)
        devices = self.resolve_targets(target_set)
        if not devices:
            raise CampaignServiceError("No devices matched the provided target set.")

        self._validate_device_compatibility(release, devices)
        rollout_stages = self._build_rollout_stages(
            strategy=strategy,
            devices=devices,
            batch_size=batch_size,
            canary_size=canary_size,
        )

        with transaction.atomic():
            list(
                ConnectDevice.objects.select_for_update()
                .filter(pk__in=[device.pk for device in devices])
                .only("pk")
            )
            self._validate_no_conflicts(devices, override_conflicts=override_conflicts)
            campaign = ConnectUpdateCampaign.objects.create(
                release=release,
                target_set=target_set,
                strategy=strategy,
                status=ConnectUpdateCampaign.Status.DRAFT,
                created_by=created_by,
                notes=notes,
            )

            initial_stage_device_ids = set(rollout_stages[0].device_ids)
            queued_at = timezone.now()
            deployments = [
                ConnectUpdateDeployment(
                    campaign=campaign,
                    device=device,
                    status=ConnectUpdateDeployment.Status.PENDING,
                    queued_at=queued_at,
                )
                for device in devices
                if device.pk in initial_stage_device_ids
            ]
            ConnectUpdateDeployment.objects.bulk_create(deployments)

            self._log_campaign_event(
                campaign=campaign,
                created_by=created_by,
                event_type=self.EVENT_CAMPAIGN_CREATED,
                payload={
                    "strategy": strategy,
                    "target_count": len(devices),
                    "rollout_stages": [
                        {"label": stage.label, "device_ids": stage.device_ids}
                        for stage in rollout_stages
                    ],
                },
            )
            self._log_campaign_event(
                campaign=campaign,
                created_by=created_by,
                event_type="campaign.scheduled",
                payload={"deployment_count": len(deployments)},
            )

        return campaign

    def resolve_targets(self, target_set: dict) -> list[ConnectDevice]:
        """Resolve explicit device ids, labels, and cohorts into device records."""

        device_ids = self._target_values(target_set, "device_ids")
        labels = self._target_values(target_set, "labels")
        cohorts = self._target_values(target_set, "cohorts")

        selected_devices = {}

        if device_ids:
            for device in ConnectDevice.objects.filter(device_id__in=device_ids):
                selected_devices[device.pk] = device

        if labels or cohorts:
            if connection.vendor == "postgresql":
                query = Q()
                if cohorts:
                    query |= Q(metadata__cohort__in=cohorts)
                for label in labels:
                    query |= Q(metadata__contains={"labels": [label]})
                for device in ConnectDevice.objects.filter(query):
                    selected_devices[device.pk] = device
            else:
                label_set = set(labels)
                cohort_set = set(cohorts)
                for device in ConnectDevice.objects.exclude(metadata=None):
                    metadata = device.metadata or {}
                    device_labels = set(metadata.get("labels") or [])
                    device_cohort = metadata.get("cohort")
                    if label_set.intersection(device_labels) or device_cohort in cohort_set:
                        selected_devices[device.pk] = device

        return sorted(selected_devices.values(), key=lambda item: item.device_id)

    def start_campaign(self, campaign: ConnectUpdateCampaign, *, created_by=None) -> ConnectUpdateCampaign:
        """Move campaign into running state."""

        with transaction.atomic():
            self._transition_campaign(
                campaign,
                to_status=ConnectUpdateCampaign.Status.RUNNING,
                event_type="campaign.started",
                created_by=created_by,
            )
            if campaign.started_at is None:
                campaign.started_at = timezone.now()
                campaign.save(update_fields=["started_at", "updated_at"])
            self._queue_next_rollout_stage(campaign=campaign)
        return campaign

    def pause_campaign(self, campaign: ConnectUpdateCampaign, *, created_by=None) -> ConnectUpdateCampaign:
        """Pause active rollout execution."""

        self._transition_campaign(
            campaign,
            to_status=ConnectUpdateCampaign.Status.PAUSED,
            event_type="campaign.paused",
            created_by=created_by,
        )
        return campaign

    def resume_campaign(self, campaign: ConnectUpdateCampaign, *, created_by=None) -> ConnectUpdateCampaign:
        """Resume a paused campaign."""

        with transaction.atomic():
            self._transition_campaign(
                campaign,
                to_status=ConnectUpdateCampaign.Status.RUNNING,
                event_type="campaign.resumed",
                created_by=created_by,
                required_from_status=ConnectUpdateCampaign.Status.PAUSED,
            )
            self._queue_next_rollout_stage(campaign=campaign)
        return campaign

    def stop_campaign(self, campaign: ConnectUpdateCampaign, *, created_by=None) -> ConnectUpdateCampaign:
        """Stop execution and mark remaining deployments rolled back."""

        with transaction.atomic():
            self._transition_campaign(
                campaign,
                to_status=ConnectUpdateCampaign.Status.STOPPED,
                event_type="campaign.stopped",
                created_by=created_by,
            )
            self._mark_open_deployments_as_rolled_back(campaign=campaign, created_by=created_by)
        return campaign

    def cancel_campaign(self, campaign: ConnectUpdateCampaign, *, created_by=None) -> ConnectUpdateCampaign:
        """Cancel campaign and mark in-flight work as rolled back."""

        with transaction.atomic():
            self._transition_campaign(
                campaign,
                to_status=ConnectUpdateCampaign.Status.CANCELLED,
                event_type="campaign.cancelled",
                created_by=created_by,
            )
            self._mark_open_deployments_as_rolled_back(campaign=campaign, created_by=created_by)
        return campaign

    def campaign_summary(self, campaign: ConnectUpdateCampaign) -> dict:
        """Return aggregate campaign and device status summary for dashboards."""

        status_counts = {
            entry["status"]: entry["count"]
            for entry in campaign.deployments.order_by().values("status").annotate(count=Count("id"))
        }
        per_device = [
            {
                "device_id": deployment.device.device_id,
                "status": deployment.status,
                "queued_at": deployment.queued_at,
                "started_at": deployment.started_at,
                "completed_at": deployment.completed_at,
            }
            for deployment in campaign.deployments.select_related("device").order_by("device__device_id")
        ]

        return {
            "campaign_id": campaign.pk,
            "status": campaign.status,
            "strategy": campaign.strategy,
            "release": {
                "id": campaign.release_id,
                "name": campaign.release.name,
                "version": campaign.release.version,
            },
            "counts": status_counts,
            "total_devices": len(per_device),
            "per_device": per_device,
        }

    def _validate_release(self, release: ConnectImageRelease) -> None:
        if not release.artifact_url:
            raise CampaignServiceError("Release artifact URI is required before scheduling.")
        if not release.checksum:
            raise CampaignServiceError("Release checksum is required before scheduling.")

    def _validate_device_compatibility(
        self,
        release: ConnectImageRelease,
        devices: list[ConnectDevice],
    ) -> None:
        compatibility_tags = release.compatibility_tags or []
        if not compatibility_tags:
            return

        incompatible = []
        normalized_tags = {tag.lower() for tag in compatibility_tags}
        for device in devices:
            hardware = (device.hardware_model or "").lower()
            operating_system = (device.os_release or "").lower()
            if hardware in normalized_tags or operating_system in normalized_tags:
                continue
            incompatible.append(device.device_id)

        if incompatible:
            raise CampaignServiceError(
                f"Release is incompatible with devices: {', '.join(sorted(incompatible))}."
            )

    def _validate_no_conflicts(
        self,
        devices: list[ConnectDevice],
        *,
        override_conflicts: bool,
    ) -> None:
        if override_conflicts:
            return

        requested_devices = {device.pk: device.device_id for device in devices}
        requested_device_ids = set(requested_devices)

        conflict_qs = ConnectUpdateDeployment.objects.filter(
            device_id__in=requested_device_ids,
            campaign__status__in=self.ACTIVE_CAMPAIGN_STATUSES,
        ).values_list("device__device_id", flat=True)
        conflicts = set(conflict_qs)

        active_campaigns = ConnectUpdateCampaign.objects.filter(
            status__in=self.ACTIVE_CAMPAIGN_STATUSES,
        ).prefetch_related("events")
        for active_campaign in active_campaigns:
            target_device_ids = self._campaign_target_device_ids(active_campaign)
            for target_device_id in target_device_ids.intersection(requested_device_ids):
                conflicts.add(requested_devices[target_device_id])

        if conflicts:
            raise CampaignServiceError(
                "Conflicting active campaigns exist for devices: "
                f"{', '.join(sorted(conflicts))}. Use explicit admin override to proceed."
            )

    def _build_rollout_stages(
        self,
        *,
        strategy: str,
        devices: list[ConnectDevice],
        batch_size: int,
        canary_size: int,
    ) -> list[RolloutStage]:
        device_pks = [device.pk for device in devices]
        if strategy == ConnectUpdateCampaign.Strategy.ALL_AT_ONCE:
            return [RolloutStage(label="full", device_ids=device_pks)]

        if strategy == ConnectUpdateCampaign.Strategy.CANARY:
            canary_count = min(max(canary_size, 1), len(device_pks))
            return [
                RolloutStage(label="canary", device_ids=device_pks[:canary_count]),
                RolloutStage(label="remaining", device_ids=device_pks[canary_count:]),
            ]

        if strategy == ConnectUpdateCampaign.Strategy.BATCHED:
            if batch_size < 1:
                raise CampaignServiceError("batch_size must be greater than zero for batched rollouts.")
            stages = []
            for index in range(0, len(device_pks), batch_size):
                stages.append(
                    RolloutStage(
                        label=f"batch_{(index // batch_size) + 1}",
                        device_ids=device_pks[index : index + batch_size],
                    )
                )
            return stages

        raise CampaignServiceError(f"Unsupported strategy: {strategy}.")

    def _target_values(self, target_set: dict, key: str) -> list[str]:
        """Validate and return list values for target set selectors."""

        values = target_set.get(key) or []
        if isinstance(values, str) or not isinstance(values, list):
            raise CampaignServiceError(f"target_set.{key} must be a list.")
        return values

    def _campaign_target_device_ids(self, campaign: ConnectUpdateCampaign) -> set[int]:
        """Return immutable target device IDs captured for an existing campaign."""

        created_event = next(
            (event for event in campaign.events.all() if event.event_type == self.EVENT_CAMPAIGN_CREATED),
            None,
        )
        if created_event:
            rollout_stage_ids = {
                device_id
                for stage in created_event.payload.get("rollout_stages", [])
                for device_id in stage.get("device_ids", [])
                if isinstance(device_id, int)
            }
            if rollout_stage_ids:
                return rollout_stage_ids
        return set(campaign.deployments.values_list("device_id", flat=True))

    def sync_rollout_progress(self, *, campaign_id: int) -> None:
        """Queue the next rollout stage when a running campaign has no open deployments."""

        with transaction.atomic():
            campaign = ConnectUpdateCampaign.objects.select_for_update().filter(pk=campaign_id).first()
            if not campaign or campaign.status != ConnectUpdateCampaign.Status.RUNNING:
                return
            self._queue_next_rollout_stage(campaign=campaign)

    def _transition_campaign(
        self,
        campaign: ConnectUpdateCampaign,
        *,
        to_status: str,
        event_type: str,
        created_by,
        required_from_status: str | None = None,
    ) -> None:
        with transaction.atomic():
            locked_campaign = type(campaign).objects.select_for_update().get(pk=campaign.pk)
            from_status = locked_campaign.status
            if required_from_status and from_status != required_from_status:
                raise CampaignServiceError(
                    f"Campaign must be {required_from_status} before transition: {from_status}."
                )
            allowed = self.CAMPAIGN_TRANSITIONS.get(from_status, set())
            if to_status not in allowed:
                raise CampaignServiceError(
                    f"Invalid campaign transition: {from_status} -> {to_status}."
                )

            locked_campaign.status = to_status
            update_fields = ["status", "updated_at"]
            if to_status in self.TERMINAL_CAMPAIGN_STATUSES and locked_campaign.completed_at is None:
                locked_campaign.completed_at = timezone.now()
                update_fields.append("completed_at")
            locked_campaign.save(update_fields=update_fields)
            campaign.status = locked_campaign.status
            campaign.completed_at = locked_campaign.completed_at

            self._log_campaign_event(
                campaign=locked_campaign,
                created_by=created_by,
                event_type=event_type,
                from_status=from_status,
                to_status=to_status,
            )

    def _queue_next_rollout_stage(self, *, campaign: ConnectUpdateCampaign) -> None:
        """Create pending deployments for the next not-yet-scheduled rollout stage."""
        stages = self._load_rollout_stages(campaign)
        if not stages:
            return

        deployment_queryset = campaign.deployments
        active_statuses = {
            ConnectUpdateDeployment.Status.PENDING,
            ConnectUpdateDeployment.Status.IN_PROGRESS,
        }
        if deployment_queryset.filter(status__in=active_statuses).exists():
            return

        failure_statuses = {
            ConnectUpdateDeployment.Status.FAILED,
            ConnectUpdateDeployment.Status.ROLLED_BACK,
        }
        if deployment_queryset.filter(status__in=failure_statuses).exists():
            self._transition_campaign(
                campaign,
                to_status=ConnectUpdateCampaign.Status.FAILED,
                event_type="campaign.failed",
                created_by=None,
            )
            return

        deployment_device_ids = set(deployment_queryset.values_list("device_id", flat=True))
        for stage in stages:
            stage_device_ids = set(stage.device_ids)
            if stage_device_ids.issubset(deployment_device_ids):
                continue

            next_ids = sorted(stage_device_ids - deployment_device_ids)
            if not next_ids:
                continue
            existing_device_ids = set(
                ConnectDevice.objects.filter(pk__in=next_ids).values_list("pk", flat=True)
            )
            missing_device_ids = sorted(set(next_ids) - existing_device_ids)
            if missing_device_ids:
                self._transition_campaign(
                    campaign,
                    to_status=ConnectUpdateCampaign.Status.FAILED,
                    event_type="campaign.failed",
                    created_by=None,
                )
                self._log_campaign_event(
                    campaign=campaign,
                    event_type="campaign.stage_queue_failed_missing_devices",
                    payload={"missing_device_ids": missing_device_ids},
                )
                return
            queued_at = timezone.now()
            ConnectUpdateDeployment.objects.bulk_create(
                [
                    ConnectUpdateDeployment(
                        campaign=campaign,
                        device_id=device_pk,
                        status=ConnectUpdateDeployment.Status.PENDING,
                        queued_at=queued_at,
                    )
                    for device_pk in sorted(existing_device_ids)
                ]
            )
            return

        self._transition_campaign(
            campaign,
            to_status=ConnectUpdateCampaign.Status.COMPLETED,
            event_type="campaign.completed",
            created_by=None,
        )

    def _load_rollout_stages(self, campaign: ConnectUpdateCampaign) -> list[RolloutStage]:
        """Load rollout stages from the creation event payload."""

        created_event = (
            campaign.events.filter(event_type=self.EVENT_CAMPAIGN_CREATED).only("payload").first()
        )
        if not created_event:
            return []

        stages = []
        for stage in created_event.payload.get("rollout_stages", []):
            device_ids = stage.get("device_ids", [])
            if not isinstance(device_ids, list):
                continue
            stages.append(RolloutStage(label=stage.get("label", ""), device_ids=device_ids))
        return stages

    def _mark_open_deployments_as_rolled_back(self, *, campaign: ConnectUpdateCampaign, created_by) -> None:
        open_statuses = {
            ConnectUpdateDeployment.Status.PENDING,
            ConnectUpdateDeployment.Status.IN_PROGRESS,
        }
        open_deployments = list(
            campaign.deployments.select_for_update().filter(status__in=open_statuses).only("id", "status")
        )
        if not open_deployments:
            return

        completed_at = timezone.now()
        rollout_status = ConnectUpdateDeployment.Status.ROLLED_BACK
        deployment_ids = [deployment.pk for deployment in open_deployments]
        campaign.deployments.filter(
            pk__in=deployment_ids,
            status__in=open_statuses,
        ).update(
            status=rollout_status,
            completed_at=completed_at,
            updated_at=completed_at,
        )
        ConnectCampaignEvent.objects.bulk_create(
            [
                ConnectCampaignEvent(
                    campaign=campaign,
                    deployment_id=deployment.pk,
                    created_by=created_by,
                    event_type="deployment.rolled_back",
                    from_status=deployment.status,
                    to_status=rollout_status,
                )
                for deployment in open_deployments
            ]
        )

    def _log_campaign_event(
        self,
        *,
        campaign: ConnectUpdateCampaign,
        event_type: str,
        created_by=None,
        deployment: ConnectUpdateDeployment | None = None,
        from_status: str = "",
        to_status: str = "",
        payload: dict | None = None,
    ) -> ConnectCampaignEvent:
        return ConnectCampaignEvent.objects.create(
            campaign=campaign,
            deployment=deployment,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            payload=payload or {},
            created_by=created_by,
        )
