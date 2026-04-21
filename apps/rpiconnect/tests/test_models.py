from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.rpiconnect.models import (
    ConnectAccount,
    ConnectDevice,
    ConnectImageRelease,
    ConnectUpdateCampaign,
    ConnectUpdateDeployment,
)


class ConnectUpdateDeploymentModelTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(
            username="rpiconnect-admin",
            email="rpiconnect-admin@example.com",
            password="password123",
        )
        self.account = ConnectAccount.objects.create(
            name="Ops Account",
            account_type=ConnectAccount.AccountType.ORG,
            organization_name="Arthexis Ops",
            token_reference="vault://connect/token/ops",
        )
        self.device = ConnectDevice.objects.create(
            account=self.account,
            device_id="pi-001",
            hardware_model="Raspberry Pi 5",
            os_release="Bookworm",
        )
        self.release = ConnectImageRelease.objects.create(
            name="edge-release",
            version="2026.04.1",
            artifact_url="https://example.com/artifacts/edge-release.img.xz",
            checksum="abc123",
        )
        self.campaign = ConnectUpdateCampaign.objects.create(
            release=self.release,
            target_set={"device_ids": [self.device.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.CANARY,
            created_by=self.user,
        )

    def test_allows_forward_status_transition(self) -> None:
        deployment = ConnectUpdateDeployment.objects.create(
            campaign=self.campaign,
            device=self.device,
        )

        deployment.status = ConnectUpdateDeployment.Status.IN_PROGRESS
        deployment.save()

        deployment.refresh_from_db()
        self.assertEqual(deployment.status, ConnectUpdateDeployment.Status.IN_PROGRESS)

    def test_rejects_invalid_status_regression(self) -> None:
        deployment = ConnectUpdateDeployment.objects.create(
            campaign=self.campaign,
            device=self.device,
            status=ConnectUpdateDeployment.Status.SUCCEEDED,
        )

        deployment.status = ConnectUpdateDeployment.Status.IN_PROGRESS

        with self.assertRaises(ValidationError):
            deployment.save()
