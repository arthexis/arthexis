"""Ingestion and reconciliation services for Raspberry Pi Connect events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.rpiconnect.models import (
    ConnectCampaignEvent,
    ConnectDevice,
    ConnectIngestionEvent,
    ConnectUpdateCampaign,
    ConnectUpdateDeployment,
)


@dataclass(frozen=True)
class ReconciliationResult:
    """Summary counters emitted after a reconciliation run."""

    checked: int = 0
    repaired: int = 0


class IngestionServiceError(ValidationError):
    """Raised when an inbound ingestion payload is invalid."""


class IngestionService:
    """Handle inbound events and status reconciliation for update deployments."""

    STATUS_MAP: dict[str, str] = {
        "queued": ConnectUpdateDeployment.Status.PENDING,
        "pending": ConnectUpdateDeployment.Status.PENDING,
        "downloading": ConnectUpdateDeployment.Status.IN_PROGRESS,
        "in_progress": ConnectUpdateDeployment.Status.IN_PROGRESS,
        "applying": ConnectUpdateDeployment.Status.IN_PROGRESS,
        "rebooting": ConnectUpdateDeployment.Status.IN_PROGRESS,
        "success": ConnectUpdateDeployment.Status.SUCCEEDED,
        "succeeded": ConnectUpdateDeployment.Status.SUCCEEDED,
        "failed": ConnectUpdateDeployment.Status.FAILED,
        "rolled_back": ConnectUpdateDeployment.Status.ROLLED_BACK,
    }

    FAILURE_KEYWORDS: tuple[tuple[str, str], ...] = (
        ("artifact", ConnectUpdateDeployment.FailureClassification.ARTIFACT_FETCH),
        ("download", ConnectUpdateDeployment.FailureClassification.ARTIFACT_FETCH),
        ("network", ConnectUpdateDeployment.FailureClassification.NETWORK),
        ("connect", ConnectUpdateDeployment.FailureClassification.NETWORK),
        ("verify", ConnectUpdateDeployment.FailureClassification.VERIFICATION),
        ("checksum", ConnectUpdateDeployment.FailureClassification.VERIFICATION),
        ("reboot", ConnectUpdateDeployment.FailureClassification.APPLY_REBOOT),
        ("apply", ConnectUpdateDeployment.FailureClassification.APPLY_REBOOT),
    )

    def ingest_event(self, payload: dict[str, Any]) -> ConnectIngestionEvent:
        """Store an idempotent event and project normalized state to deployments."""

        normalized = self._normalize_payload(payload)
        with transaction.atomic():
            event, created = ConnectIngestionEvent.objects.get_or_create(
                event_id=normalized["event_id"],
                external_device_id=normalized["external_device_id"],
                defaults=normalized,
            )
            if not created:
                return event

            self._project_event_to_deployment(event)
            return event

    def reconcile_deployments(
        self,
        *,
        status_fetcher,
    ) -> ReconciliationResult:
        """Repair deployment status drift using an injected remote status fetcher."""

        checked = 0
        repaired = 0
        deployments = ConnectUpdateDeployment.objects.select_related("device", "campaign")
        deployments = deployments.exclude(
            status__in=[
                ConnectUpdateDeployment.Status.SUCCEEDED,
                ConnectUpdateDeployment.Status.FAILED,
                ConnectUpdateDeployment.Status.ROLLED_BACK,
            ]
        )
        for deployment in deployments:
            checked += 1
            remote_status = status_fetcher(deployment)
            local_status = deployment.status
            normalized_status = self._normalize_status(remote_status)
            if not normalized_status or normalized_status == local_status:
                continue

            try:
                with transaction.atomic():
                    deployment.status = normalized_status
                    if normalized_status in {
                        ConnectUpdateDeployment.Status.SUCCEEDED,
                        ConnectUpdateDeployment.Status.FAILED,
                        ConnectUpdateDeployment.Status.ROLLED_BACK,
                    }:
                        deployment.completed_at = timezone.now()
                    deployment.save(
                        update_fields=[
                            "status",
                            "completed_at",
                            "updated_at",
                        ]
                    )
                    ConnectCampaignEvent.objects.create(
                        campaign=deployment.campaign,
                        deployment=deployment,
                        event_type="deployment.reconciled",
                        from_status=local_status,
                        to_status=normalized_status,
                        payload={"source": "reconciler"},
                    )
                    repaired += 1
            except ValidationError:
                continue
        return ReconciliationResult(checked=checked, repaired=repaired)

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            raise IngestionServiceError("event_id is required")

        device_id = str(payload.get("device_id") or "").strip()
        if not device_id:
            raise IngestionServiceError("device_id is required")

        event_type = str(payload.get("event_type") or "").strip().lower()
        if event_type not in ConnectIngestionEvent.EventType.values:
            raise IngestionServiceError("event_type must be update or deployment")

        status = str(payload.get("status") or "").strip().lower()
        failure_classification = self.classify_failure(payload)
        retry_attempt = self._parse_positive_int(payload.get("retry_attempt"), default=0)
        occurred_at = self._parse_datetime(payload.get("occurred_at"))

        deployment = self._resolve_deployment(payload, device_id)
        campaign = deployment.campaign if deployment else None
        device = deployment.device if deployment else ConnectDevice.objects.filter(device_id=device_id).first()

        payload_snippet = {
            "event_type": event_type,
            "status": status,
            "failure_stage": payload.get("failure_stage"),
            "error": payload.get("error"),
            "occurred_at": payload.get("occurred_at"),
        }
        normalized_payload = {
            "event_id": event_id,
            "event_type": event_type,
            "device_id": device_id,
            "status": status,
            "deployment_id": deployment.pk if deployment else None,
            "campaign_id": campaign.pk if campaign else None,
            "failure_classification": failure_classification,
        }

        return {
            "event_id": event_id,
            "event_type": event_type,
            "external_device_id": device_id,
            "device": device,
            "deployment": deployment,
            "campaign": campaign,
            "status": status,
            "failure_classification": failure_classification,
            "retry_attempt": retry_attempt,
            "cooldown_until": None,
            "payload_snippet": payload_snippet,
            "normalized_payload": normalized_payload,
            "occurred_at": occurred_at,
        }

    def _project_event_to_deployment(self, event: ConnectIngestionEvent) -> None:
        deployment = event.deployment
        if deployment is None:
            return

        normalized_status = self._normalize_status(event.status)
        if not normalized_status:
            return

        if normalized_status == ConnectUpdateDeployment.Status.FAILED:
            self._schedule_retry(
                deployment,
                failure_classification=event.failure_classification,
            )
            event.retry_attempt = deployment.retry_attempts
            event.cooldown_until = deployment.next_retry_at
            event.save(update_fields=["retry_attempt", "cooldown_until"])

        if deployment.status == normalized_status:
            return

        original_status = deployment.status
        deployment.status = normalized_status
        if normalized_status in {
            ConnectUpdateDeployment.Status.SUCCEEDED,
            ConnectUpdateDeployment.Status.FAILED,
            ConnectUpdateDeployment.Status.ROLLED_BACK,
        }:
            deployment.completed_at = timezone.now()
        deployment.save(update_fields=["status", "completed_at", "updated_at"])
        ConnectCampaignEvent.objects.create(
            campaign=deployment.campaign,
            deployment=deployment,
            event_type="deployment.ingested",
            from_status=original_status,
            to_status=normalized_status,
            payload={"ingestion_event_id": event.pk},
        )

    def _resolve_deployment(
        self,
        payload: dict[str, Any],
        device_id: str,
    ) -> ConnectUpdateDeployment | None:
        deployment_id = payload.get("deployment_id")
        if deployment_id is not None:
            deployment = ConnectUpdateDeployment.objects.select_related("campaign", "device").filter(
                pk=deployment_id
            ).first()
            if deployment:
                return deployment

        campaign_id = payload.get("campaign_id")
        if campaign_id is not None:
            deployment = ConnectUpdateDeployment.objects.select_related("campaign", "device").filter(
                campaign_id=campaign_id,
                device__device_id=device_id,
            ).first()
            if deployment:
                return deployment

        return (
            ConnectUpdateDeployment.objects.select_related("campaign", "device")
            .filter(device__device_id=device_id)
            .order_by("-updated_at")
            .first()
        )

    def _schedule_retry(self, deployment: ConnectUpdateDeployment, *, failure_classification: str) -> None:
        if deployment.retry_attempts >= deployment.retry_max_attempts:
            return
        deployment.retry_attempts += 1
        deployment.failure_classification = failure_classification
        deployment.next_retry_at = timezone.now() + timedelta(seconds=deployment.retry_cooldown_seconds)
        deployment.save(
            update_fields=[
                "retry_attempts",
                "failure_classification",
                "next_retry_at",
                "updated_at",
            ]
        )

    def _normalize_status(self, value: Any) -> str:
        return self.STATUS_MAP.get(str(value or "").strip().lower(), "")

    def classify_failure(self, payload: dict[str, Any]) -> str:
        failure_stage = str(payload.get("failure_stage") or "").strip().lower()
        if failure_stage in ConnectUpdateDeployment.FailureClassification.values:
            return failure_stage

        haystack = " ".join(
            str(payload.get(key) or "") for key in ("failure_stage", "error", "status", "details")
        ).lower()
        for keyword, classification in self.FAILURE_KEYWORDS:
            if keyword in haystack:
                return classification
        return ConnectUpdateDeployment.FailureClassification.NONE

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        parsed = parse_datetime(str(value))
        if parsed is None:
            return None
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, timezone=timezone.get_current_timezone())
        return parsed

    def _parse_positive_int(self, value: Any, *, default: int) -> int:
        if value is None:
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed >= 0 else default


def default_reconciliation_status_fetcher(deployment: ConnectUpdateDeployment) -> str:
    """Default status fetcher hook used when remote polling is unavailable."""

    metadata = deployment.error_payload if isinstance(deployment.error_payload, dict) else {}
    return str(metadata.get("remote_status") or "")
