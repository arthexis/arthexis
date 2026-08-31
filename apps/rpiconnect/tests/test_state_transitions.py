from django.test import TestCase

from apps.rpiconnect.models import (
    ConnectCampaignEvent,
    ConnectUpdateCampaign,
    ConnectUpdateDeployment,
)
from apps.rpiconnect.services import CampaignServiceError
from apps.rpiconnect.tests.test_campaign_service import CampaignServiceTestCaseMixin


class CampaignStateTransitionTests(CampaignServiceTestCaseMixin, TestCase):
    def test_pause_resume_stop_transitions_are_audited(self) -> None:
        campaign = self.service.create_campaign(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.CANARY,
            created_by=self.user,
        )

        self.service.start_campaign(campaign, created_by=self.user)
        self.service.pause_campaign(campaign, created_by=self.user)
        self.service.resume_campaign(campaign, created_by=self.user)
        self.service.stop_campaign(campaign, created_by=self.user)

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, ConnectUpdateCampaign.Status.STOPPED)
        self.assertTrue(campaign.completed_at)
        statuses = list(
            campaign.events.filter(event_type__startswith="campaign.").values_list("event_type", flat=True)
        )
        self.assertIn("campaign.paused", statuses)
        self.assertIn("campaign.resumed", statuses)
        self.assertIn("campaign.stopped", statuses)

    def test_cancel_rolls_back_open_deployments_and_logs_events(self) -> None:
        campaign = self.service.create_campaign(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.CANARY,
            created_by=self.user,
        )
        deployment = campaign.deployments.get()
        deployment.status = ConnectUpdateDeployment.Status.IN_PROGRESS
        deployment.save()

        self.service.start_campaign(campaign, created_by=self.user)
        self.service.cancel_campaign(campaign, created_by=self.user)

        deployment.refresh_from_db()
        self.assertEqual(deployment.status, ConnectUpdateDeployment.Status.ROLLED_BACK)
        self.assertTrue(
            ConnectCampaignEvent.objects.filter(
                campaign=campaign,
                deployment=deployment,
                event_type="deployment.rolled_back",
            ).exists()
        )

    def test_rejects_invalid_transition(self) -> None:
        campaign = self.service.create_campaign(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.CANARY,
            created_by=self.user,
        )

        with self.assertRaises(CampaignServiceError):
            self.service.resume_campaign(campaign, created_by=self.user)

    def test_running_campaign_auto_queues_next_canary_stage_when_first_stage_finished(self) -> None:
        campaign = self.service.create_campaign(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id, self.device_b.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.CANARY,
            canary_size=1,
            created_by=self.user,
        )
        self.service.start_campaign(campaign, created_by=self.user)

        first_deployment = campaign.deployments.get(device=self.device_a)
        first_deployment.status = ConnectUpdateDeployment.Status.IN_PROGRESS
        first_deployment.save(update_fields=["status", "updated_at"])
        first_deployment.status = ConnectUpdateDeployment.Status.SUCCEEDED
        with self.captureOnCommitCallbacks(execute=True):
            first_deployment.save(update_fields=["status", "updated_at"])

        self.assertEqual(campaign.deployments.count(), 2)
        self.assertTrue(campaign.deployments.filter(device=self.device_b).exists())

    def test_running_campaign_stops_stage_progression_after_failed_canary(self) -> None:
        campaign = self.service.create_campaign(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id, self.device_b.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.CANARY,
            canary_size=1,
            created_by=self.user,
        )
        self.service.start_campaign(campaign, created_by=self.user)

        first_deployment = campaign.deployments.get(device=self.device_a)
        first_deployment.status = ConnectUpdateDeployment.Status.IN_PROGRESS
        first_deployment.save(update_fields=["status", "updated_at"])
        first_deployment.status = ConnectUpdateDeployment.Status.FAILED
        with self.captureOnCommitCallbacks(execute=True):
            first_deployment.save(update_fields=["status", "updated_at"])

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, ConnectUpdateCampaign.Status.FAILED)
        self.assertEqual(campaign.deployments.count(), 1)
        self.assertFalse(campaign.deployments.filter(device=self.device_b).exists())

    def test_running_campaign_marks_complete_after_final_stage_finishes(self) -> None:
        campaign = self.service.create_campaign(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id, self.device_b.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.CANARY,
            canary_size=1,
            created_by=self.user,
        )
        self.service.start_campaign(campaign, created_by=self.user)

        first_deployment = campaign.deployments.get(device=self.device_a)
        first_deployment.status = ConnectUpdateDeployment.Status.IN_PROGRESS
        first_deployment.save(update_fields=["status", "updated_at"])
        first_deployment.status = ConnectUpdateDeployment.Status.SUCCEEDED
        with self.captureOnCommitCallbacks(execute=True):
            first_deployment.save(update_fields=["status", "updated_at"])

        second_deployment = campaign.deployments.get(device=self.device_b)
        second_deployment.status = ConnectUpdateDeployment.Status.IN_PROGRESS
        second_deployment.save(update_fields=["status", "updated_at"])
        second_deployment.status = ConnectUpdateDeployment.Status.SUCCEEDED
        with self.captureOnCommitCallbacks(execute=True):
            second_deployment.save(update_fields=["status", "updated_at"])

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, ConnectUpdateCampaign.Status.COMPLETED)
        self.assertTrue(campaign.completed_at)
        self.assertTrue(campaign.events.filter(event_type="campaign.completed").exists())

    def test_running_campaign_fails_if_next_stage_devices_were_deleted(self) -> None:
        campaign = self.service.create_campaign(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id, self.device_b.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.CANARY,
            canary_size=1,
            created_by=self.user,
        )
        self.service.start_campaign(campaign, created_by=self.user)

        first_deployment = campaign.deployments.get(device=self.device_a)
        self.device_b.delete()
        first_deployment.status = ConnectUpdateDeployment.Status.IN_PROGRESS
        first_deployment.save(update_fields=["status", "updated_at"])
        first_deployment.status = ConnectUpdateDeployment.Status.SUCCEEDED
        with self.captureOnCommitCallbacks(execute=True):
            first_deployment.save(update_fields=["status", "updated_at"])

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, ConnectUpdateCampaign.Status.FAILED)
        self.assertTrue(
            campaign.events.filter(event_type="campaign.stage_queue_failed_missing_devices").exists()
        )
        self.assertEqual(campaign.deployments.count(), 1)

    def test_terminal_deployment_status_creates_durable_event(self) -> None:
        campaign = self.service.create_campaign(
            release=self.release,
            target_set={"device_ids": [self.device_a.device_id]},
            strategy=ConnectUpdateCampaign.Strategy.ALL_AT_ONCE,
            created_by=self.user,
        )
        deployment = campaign.deployments.get()

        deployment.status = ConnectUpdateDeployment.Status.IN_PROGRESS
        deployment.save(update_fields=["status", "updated_at"])
        deployment.status = ConnectUpdateDeployment.Status.SUCCEEDED
        with self.captureOnCommitCallbacks(execute=True):
            deployment.save(update_fields=["status", "updated_at"])

        self.assertTrue(
            ConnectCampaignEvent.objects.filter(
                campaign=campaign,
                deployment=deployment,
                event_type="deployment.succeeded",
                from_status=ConnectUpdateDeployment.Status.IN_PROGRESS,
                to_status=ConnectUpdateDeployment.Status.SUCCEEDED,
            ).exists()
        )
