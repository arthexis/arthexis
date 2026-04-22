from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.rpiconnect.models import (
    ConnectAccount,
    ConnectCampaignEvent,
    ConnectDevice,
    ConnectImageRelease,
    ConnectIngestionEvent,
    ConnectUpdateCampaign,
    ConnectUpdateDeployment,
)


@override_settings(RPICONNECT_INGESTION_TOKEN="shared-secret")
class IngestionApiTests(TestCase):
    def setUp(self) -> None:
        self.account = ConnectAccount.objects.create(
            name="Ops",
            account_type=ConnectAccount.AccountType.ORG,
            token_reference="vault://ops",
        )
        self.device = ConnectDevice.objects.create(
            account=self.account,
            device_id="pi-001",
            hardware_model="Raspberry Pi 5",
        )
        self.release = ConnectImageRelease.objects.create(
            name="edge-release",
            version="2026.04.2",
            artifact_url="https://example.com/release.img.xz",
            checksum="sha256:abc123",
        )
        self.campaign = ConnectUpdateCampaign.objects.create(
            release=self.release,
            target_set={"device_ids": [self.device.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.ALL_AT_ONCE,
        )
        self.deployment = ConnectUpdateDeployment.objects.create(
            campaign=self.campaign,
            device=self.device,
            status=ConnectUpdateDeployment.Status.IN_PROGRESS,
        )
        self.url = reverse("rpiconnect-ingestion-events")

    def test_requires_authentication_token(self) -> None:
        response = self.client.post(self.url, data={"event_id": "evt-1", "device_id": "pi-001"}, content_type="application/json")

        self.assertEqual(response.status_code, 401)

    @override_settings(RPICONNECT_INGESTION_TOKEN="")
    def test_fails_closed_when_token_is_not_configured(self) -> None:
        response = self.client.post(
            self.url,
            data={"event_id": "evt-1", "device_id": "pi-001"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 500)

    def test_ingests_event_idempotently_and_projects_status(self) -> None:
        payload = {
            "event_id": "evt-123",
            "event_type": "deployment",
            "device_id": self.device.device_id,
            "deployment_id": self.deployment.pk,
            "status": "succeeded",
            "occurred_at": timezone.now().isoformat(),
        }

        first = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer shared-secret",
        )
        second = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer shared-secret",
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(ConnectIngestionEvent.objects.count(), 1)

        self.deployment.refresh_from_db()
        self.assertEqual(self.deployment.status, ConnectUpdateDeployment.Status.SUCCEEDED)
        self.assertTrue(
            ConnectCampaignEvent.objects.filter(
                deployment=self.deployment,
                event_type="deployment.ingested",
            ).exists()
        )

    def test_classifies_failures_and_applies_bounded_retry_hooks(self) -> None:
        payload = {
            "event_id": "evt-failure",
            "event_type": "deployment",
            "device_id": self.device.device_id,
            "deployment_id": self.deployment.pk,
            "status": "failed",
            "failure_stage": "artifact",
            "error": "artifact download timeout",
        }

        response = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer shared-secret",
        )

        self.assertEqual(response.status_code, 202)

        self.deployment.refresh_from_db()
        self.assertEqual(
            self.deployment.failure_classification,
            ConnectUpdateDeployment.FailureClassification.ARTIFACT_FETCH,
        )
        self.assertEqual(self.deployment.retry_attempts, 1)
        self.assertIsNotNone(self.deployment.next_retry_at)

        event = ConnectIngestionEvent.objects.get(
            event_id="evt-failure",
            external_device_id=self.device.device_id,
        )
        self.assertEqual(
            event.failure_classification,
            ConnectUpdateDeployment.FailureClassification.ARTIFACT_FETCH,
        )
        self.assertEqual(event.retry_attempt, 1)
        self.assertIsNotNone(event.cooldown_until)
        self.assertIn("error", event.payload_snippet)
        self.assertEqual(event.normalized_payload["deployment_id"], self.deployment.pk)

    def test_duplicate_failed_event_does_not_consume_additional_retries(self) -> None:
        payload = {
            "event_id": "evt-duplicate-failure",
            "event_type": "deployment",
            "device_id": self.device.device_id,
            "deployment_id": self.deployment.pk,
            "status": "failed",
            "failure_stage": "artifact",
            "error": "artifact download timeout",
        }

        first = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer shared-secret",
        )
        second = self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer shared-secret",
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(ConnectIngestionEvent.objects.count(), 1)

        self.deployment.refresh_from_db()
        self.assertEqual(self.deployment.retry_attempts, 1)

    def test_returns_400_for_invalid_status_transition(self) -> None:
        self.deployment.status = ConnectUpdateDeployment.Status.SUCCEEDED
        self.deployment.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            self.url,
            data={
                "event_id": "evt-stale",
                "event_type": "deployment",
                "device_id": self.device.device_id,
                "deployment_id": self.deployment.pk,
                "status": "in_progress",
            },
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer shared-secret",
        )

        self.assertEqual(response.status_code, 400)

    def test_ignores_mismatched_deployment_id_and_resolves_by_device(self) -> None:
        other_device = ConnectDevice.objects.create(
            account=self.account,
            device_id="pi-099",
            hardware_model="Raspberry Pi 4",
        )
        mismatched_deployment = ConnectUpdateDeployment.objects.create(
            campaign=self.campaign,
            device=other_device,
            status=ConnectUpdateDeployment.Status.IN_PROGRESS,
        )

        response = self.client.post(
            self.url,
            data={
                "event_id": "evt-mismatch",
                "event_type": "deployment",
                "device_id": self.device.device_id,
                "deployment_id": mismatched_deployment.pk,
                "status": "succeeded",
            },
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer shared-secret",
        )

        self.assertEqual(response.status_code, 202)
        event = ConnectIngestionEvent.objects.get(event_id="evt-mismatch")
        self.assertEqual(event.deployment_id, self.deployment.pk)

    def test_returns_400_for_out_of_range_retry_attempt(self) -> None:
        response = self.client.post(
            self.url,
            data={
                "event_id": "evt-overflow",
                "event_type": "deployment",
                "device_id": self.device.device_id,
                "deployment_id": self.deployment.pk,
                "status": "failed",
                "retry_attempt": 100000,
            },
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer shared-secret",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ConnectIngestionEvent.objects.count(), 0)
