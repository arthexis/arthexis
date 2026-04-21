from apps.rpiconnect.models import (
    ConnectCampaignEvent,
    ConnectUpdateCampaign,
    ConnectUpdateDeployment,
)
from apps.rpiconnect.services import CampaignServiceError
from apps.rpiconnect.tests.test_campaign_service import CampaignServiceTests


class CampaignStateTransitionTests(CampaignServiceTests):
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
