from __future__ import annotations

from django.core.management import call_command
from django.test import TestCase

from apps.rpiconnect.models import (
    ConnectAccount,
    ConnectCampaignEvent,
    ConnectDevice,
    ConnectImageRelease,
    ConnectUpdateCampaign,
    ConnectUpdateDeployment,
)
from apps.rpiconnect.services.ingestion_service import IngestionService


class ReconciliationTests(TestCase):
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
            status=ConnectUpdateCampaign.Status.RUNNING,
        )
        self.deployment = ConnectUpdateDeployment.objects.create(
            campaign=self.campaign,
            device=self.device,
            status=ConnectUpdateDeployment.Status.IN_PROGRESS,
            error_payload={"remote_status": "succeeded"},
        )

    def test_reconciliation_repairs_status_drift(self) -> None:
        service = IngestionService()

        result = service.reconcile_deployments(
            status_fetcher=lambda deployment: "succeeded" if deployment.pk == self.deployment.pk else "",
        )

        self.assertEqual(result.checked, 1)
        self.assertEqual(result.repaired, 1)

        self.deployment.refresh_from_db()
        self.assertEqual(self.deployment.status, ConnectUpdateDeployment.Status.SUCCEEDED)
        self.assertTrue(
            ConnectCampaignEvent.objects.filter(
                deployment=self.deployment,
                event_type="deployment.reconciled",
            ).exists()
        )

    def test_reconciliation_only_checks_non_terminal_deployments(self) -> None:
        ConnectUpdateDeployment.objects.create(
            campaign=self.campaign,
            device=ConnectDevice.objects.create(
                account=self.account,
                device_id="pi-002",
                hardware_model="Raspberry Pi 4",
            ),
            status=ConnectUpdateDeployment.Status.SUCCEEDED,
        )
        service = IngestionService()

        result = service.reconcile_deployments(status_fetcher=lambda deployment: "succeeded")

        self.assertEqual(result.checked, 1)

    def test_reconciliation_continues_after_invalid_transition(self) -> None:
        pending_deployment = ConnectUpdateDeployment.objects.create(
            campaign=self.campaign,
            device=ConnectDevice.objects.create(
                account=self.account,
                device_id="pi-003",
                hardware_model="Raspberry Pi 3",
            ),
            status=ConnectUpdateDeployment.Status.PENDING,
        )

        service = IngestionService()
        result = service.reconcile_deployments(
            status_fetcher=lambda deployment: (
                "succeeded"
                if deployment.pk == pending_deployment.pk
                else "failed"
            ),
        )

        self.assertEqual(result.checked, 2)
        self.assertEqual(result.repaired, 1)

        self.deployment.refresh_from_db()
        pending_deployment.refresh_from_db()
        self.assertEqual(self.deployment.status, ConnectUpdateDeployment.Status.FAILED)
        self.assertEqual(pending_deployment.status, ConnectUpdateDeployment.Status.PENDING)

    def test_reconciliation_command_uses_default_polling_hook(self) -> None:
        call_command("reconcile_rpiconnect")

        self.deployment.refresh_from_db()
        self.assertEqual(self.deployment.status, ConnectUpdateDeployment.Status.SUCCEEDED)
