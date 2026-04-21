from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.rpiconnect.models import (
    ConnectAccount,
    ConnectCampaignEvent,
    ConnectDevice,
    ConnectImageRelease,
    ConnectUpdateCampaign,
)
from apps.rpiconnect.services import CampaignService, CampaignServiceError


class CampaignServiceTestCaseMixin:
    def setUp(self) -> None:
        self.service = CampaignService()
        self.user = get_user_model().objects.create_user(
            username="campaign-admin",
            password="password123",
        )
        self.account = ConnectAccount.objects.create(
            name="Ops",
            account_type=ConnectAccount.AccountType.ORG,
            token_reference="vault://ops",
        )
        self.device_a = ConnectDevice.objects.create(
            account=self.account,
            device_id="pi-001",
            hardware_model="Raspberry Pi 5",
            os_release="Bookworm",
            metadata={"labels": ["edge"], "cohort": "north"},
        )
        self.device_b = ConnectDevice.objects.create(
            account=self.account,
            device_id="pi-002",
            hardware_model="Raspberry Pi 4",
            os_release="Bullseye",
            metadata={"labels": ["stable"], "cohort": "south"},
        )
        self.release = ConnectImageRelease.objects.create(
            name="edge-release",
            version="2026.04.1",
            artifact_url="https://example.com/release.img.xz",
            checksum="deadbeef",
            compatibility_tags=[
                "raspberry pi 4",
                "raspberry pi 5",
                "bookworm",
                "bullseye",
            ],
        )


class CampaignServiceTests(CampaignServiceTestCaseMixin, TestCase):
    def test_creates_campaign_from_explicit_and_metadata_targets(self) -> None:
        campaign = self.service.create_campaign(
            release=self.release,
            target_set={
                "device_ids": [self.device_a.device_id],
                "labels": ["stable"],
                "cohorts": ["north"],
            },
            strategy=ConnectUpdateCampaign.Strategy.ALL_AT_ONCE,
            created_by=self.user,
        )

        self.assertEqual(campaign.deployments.count(), 2)
        event_types = list(campaign.events.values_list("event_type", flat=True))
        self.assertIn("campaign.created", event_types)
        self.assertIn("campaign.scheduled", event_types)

    def test_canary_strategy_schedules_only_initial_stage(self) -> None:
        campaign = self.service.create_campaign(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id, self.device_b.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.CANARY,
            canary_size=1,
            created_by=self.user,
        )

        self.assertEqual(campaign.deployments.count(), 1)
        self.assertEqual(campaign.deployments.get().device_id, self.device_a.pk)
        scheduled_event = campaign.events.get(event_type="campaign.scheduled")
        self.assertEqual(scheduled_event.payload["deployment_count"], 1)

    def test_rejects_conflicts_for_later_stages_without_override(self) -> None:
        self.service.create_campaign(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id, self.device_b.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.CANARY,
            canary_size=1,
            created_by=self.user,
        )

        with self.assertRaises(CampaignServiceError):
            self.service.create_campaign(
                release=self.release,
                target_set={"device_ids": [self.device_b.device_id]},
                strategy=ConnectUpdateCampaign.Strategy.ALL_AT_ONCE,
                created_by=self.user,
            )

    def test_rejects_conflicts_using_immutable_stage_snapshot(self) -> None:
        self.service.create_campaign(
            release=self.release,
            target_set={"labels": ["edge", "stable"]},
            strategy=ConnectUpdateCampaign.Strategy.CANARY,
            canary_size=1,
            created_by=self.user,
        )
        self.device_b.metadata = {"labels": ["renamed"], "cohort": "south"}
        self.device_b.save(update_fields=["metadata", "updated_at"])

        with self.assertRaises(CampaignServiceError):
            self.service.create_campaign(
                release=self.release,
                target_set={"device_ids": [self.device_b.device_id]},
                strategy=ConnectUpdateCampaign.Strategy.ALL_AT_ONCE,
                created_by=self.user,
            )

    def test_rejects_non_list_target_selectors(self) -> None:
        with self.assertRaisesMessage(CampaignServiceError, "target_set.device_ids must be a list."):
            self.service.create_campaign(
                release=self.release,
                target_set={"device_ids": self.device_a.device_id},
                strategy=ConnectUpdateCampaign.Strategy.ALL_AT_ONCE,
                created_by=self.user,
            )

    def test_rejects_incompatible_device_target(self) -> None:
        strict_release = ConnectImageRelease.objects.create(
            name="strict-release",
            version="2026.04.2",
            artifact_url="https://example.com/strict-release.img.xz",
            checksum="feedbeef",
            compatibility_tags=["raspberry pi 5", "bookworm"],
        )
        with self.assertRaises(CampaignServiceError):
            self.service.create_campaign(
                release=strict_release,
                target_set={"device_ids": [self.device_b.device_id]},
                strategy=ConnectUpdateCampaign.Strategy.CANARY,
                created_by=self.user,
            )

    def test_rejects_conflicts_without_override(self) -> None:
        existing = ConnectUpdateCampaign.objects.create(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.ALL_AT_ONCE,
            status=ConnectUpdateCampaign.Status.RUNNING,
            created_by=self.user,
        )
        existing.deployments.create(device=self.device_a)

        with self.assertRaises(CampaignServiceError):
            self.service.create_campaign(
                release=self.release,
                target_set={"device_ids": [self.device_a.device_id]},
                strategy=ConnectUpdateCampaign.Strategy.CANARY,
                created_by=self.user,
            )

        campaign = self.service.create_campaign(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.CANARY,
            created_by=self.user,
            override_conflicts=True,
        )
        self.assertEqual(campaign.deployments.count(), 1)

    def test_campaign_summary_provides_per_device_aggregation(self) -> None:
        campaign = self.service.create_campaign(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id, self.device_b.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.ALL_AT_ONCE,
            created_by=self.user,
        )

        summary = self.service.campaign_summary(campaign)

        self.assertEqual(summary["total_devices"], 2)
        self.assertEqual(summary["counts"]["pending"], 2)
        self.assertEqual(summary["per_device"][0]["device_id"], self.device_a.device_id)
        self.assertEqual(ConnectCampaignEvent.objects.filter(campaign=campaign).count(), 2)
