"""Regression tests for operator journey progression and admin dashboard surfacing."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.groups.models import SecurityGroup
from apps.ops.models import OperatorJourney, OperatorJourneyStep
from apps.ops.operator_journey import complete_step_for_user, status_for_user


class OperatorJourneyFlowTests(TestCase):
    """Validate linear operator journey progression and catch-up behavior."""

    def setUp(self):
        self.group = SecurityGroup.objects.create(name="Operator Journey Test Group")
        self.user = get_user_model().objects.create_user(
            username="ops-journey-user",
            password="x",
            is_staff=True,
            is_superuser=True,
        )
        self.user.groups.add(self.group)
        self.journey = OperatorJourney.objects.create(
            name="Ops Journey",
            slug="ops-journey",
            security_group=self.group,
            is_active=True,
        )
        self.step_1 = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Step 1",
            slug="step-1",
            instruction="Do step one.",
            iframe_url="/admin/",
            order=1,
        )
        self.step_2 = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Step 2",
            slug="step-2",
            instruction="Do step two.",
            iframe_url="/admin/",
            order=2,
        )

    def test_status_tracks_progress_and_catches_up_on_new_steps(self):
        status = status_for_user(user=self.user)
        self.assertEqual(status.message, "Next Operator task: Step 1")

        self.assertTrue(complete_step_for_user(user=self.user, step=self.step_1))
        status = status_for_user(user=self.user)
        self.assertEqual(status.message, "Next Operator task: Step 2")

        self.assertTrue(complete_step_for_user(user=self.user, step=self.step_2))
        status = status_for_user(user=self.user)
        self.assertTrue(status.is_complete)

        step_3 = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Step 3",
            slug="step-3",
            instruction="Do step three.",
            iframe_url="/admin/",
            order=3,
        )
        status = status_for_user(user=self.user)
        self.assertEqual(status.message, "Next Operator task: Step 3")
        self.assertIn(str(step_3.pk), status.url)

    def test_cannot_complete_out_of_order_step(self):
        completed = complete_step_for_user(user=self.user, step=self.step_2)

        self.assertFalse(completed)


class OperatorJourneyViewTests(TestCase):
    """Validate operator journey routes and dashboard link rendering."""

    def setUp(self):
        self.group = SecurityGroup.objects.create(name="Operator Journey Dashboard Group")
        self.user = get_user_model().objects.create_user(
            username="ops-journey-dashboard",
            password="x",
            is_staff=True,
            is_superuser=True,
        )
        self.user.groups.add(self.group)
        self.client.force_login(self.user)

        self.journey = OperatorJourney.objects.create(
            name="Ops Dashboard Journey",
            slug="ops-dashboard-journey",
            security_group=self.group,
            is_active=True,
        )
        self.step_1 = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Validate role",
            slug="validate-role",
            instruction="Validate the role.",
            help_text="Switch role and restart if required.",
            iframe_url="/admin/nodes/node/",
            order=1,
        )
        self.step_2 = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Confirm restart",
            slug="confirm-restart",
            instruction="Confirm service state.",
            iframe_url="/admin/",
            order=2,
        )

    def test_dashboard_shows_operator_journey_link(self):
        response = self.client.get(reverse("admin:index"))

        self.assertContains(response, "Next Operator task: Validate role")
        self.assertContains(
            response,
            reverse("ops:operator-journey-step", args=[self.step_1.pk]),
        )

    def test_step_view_redirects_when_opening_future_step(self):
        response = self.client.get(reverse("ops:operator-journey-step", args=[self.step_2.pk]))

        self.assertRedirects(response, reverse("ops:operator-journey-step", args=[self.step_1.pk]))

    def test_completing_all_steps_shows_completion_message_on_dashboard(self):
        self.client.post(reverse("ops:operator-journey-step-complete", args=[self.step_1.pk]))
        complete_response = self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_2.pk])
        )

        self.assertContains(complete_response, "Operator journey complete")

        dashboard_response = self.client.get(reverse("admin:index"))
        self.assertContains(
            dashboard_response,
            "All Operator tasks completed to date. Keep coming back for more.",
        )
