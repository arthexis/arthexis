"""Regression tests for operator journey progression and admin dashboard surfacing."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.template import Context, Template
from django.urls import reverse

from apps.groups.constants import SITE_OPERATOR_GROUP_NAME
from apps.groups.models import SecurityGroup
from apps.nodes.models import NodeRole
from apps.ops.models import OperatorJourney, OperatorJourneyStep
from apps.ops.operator_journey import complete_step_for_user, status_for_user
from apps.ops.views import _build_node_role_validation_summary
from apps.repos.models import GitHubToken


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
        self.assertEqual(status.message, "Step 1")
        self.assertEqual(status.task_title, "Step 1")
        self.assertEqual(status.available_since, self.user.date_joined)

        self.assertTrue(complete_step_for_user(user=self.user, step=self.step_1))
        status = status_for_user(user=self.user)
        self.assertEqual(status.message, "Step 2")
        self.assertEqual(status.task_title, "Step 2")
        self.assertEqual(
            status.available_since,
            self.user.operator_journey_step_completions.first().completed_at,
        )

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
        self.assertEqual(status.message, "Step 3")
        self.assertEqual(status.task_title, "Step 3")
        self.assertIn(str(step_3.pk), status.url)

    def test_cannot_complete_out_of_order_step(self):
        completed = complete_step_for_user(user=self.user, step=self.step_2)

        self.assertFalse(completed)


class OperatorJourneyViewTests(TestCase):
    """Validate operator journey routes and dashboard link rendering."""

    def setUp(self):
        self.group = SecurityGroup.objects.create(
            name="Operator Journey Dashboard Group"
        )
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
            slug="validate-local-node-role",
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

        self.assertContains(response, "Validate role")
        self.assertContains(
            response,
            reverse("ops:operator-journey-step", args=[self.step_1.pk]),
        )

    def test_step_view_redirects_when_opening_future_step(self):
        response = self.client.get(
            reverse("ops:operator-journey-step", args=[self.step_2.pk])
        )

        self.assertRedirects(
            response, reverse("ops:operator-journey-step", args=[self.step_1.pk])
        )

    def test_validate_role_step_shows_setup_check_instead_of_iframe(self):
        response = self.client.get(
            reverse("ops:operator-journey-step", args=[self.step_1.pk])
        )

        self.assertContains(
            response, "Node role changes must be applied with install/configure scripts"
        )
        self.assertContains(response, "Current config and completion command")
        self.assertContains(response, "Roles and auto-upgrade options")
        self.assertContains(response, 'name="node-role-choice"', html=False)
        self.assertContains(response, 'id="operator-upgrade-command"', html=False)
        self.assertContains(response, "./configure.sh --check")
        self.assertContains(response, "Decision flow:")
        self.assertNotContains(response, "<iframe", html=False)

    def test_validate_role_step_limits_role_choices_to_basic_configure_roles(self):
        NodeRole.objects.create(name="Gateway")
        response = self.client.get(
            reverse("ops:operator-journey-step", args=[self.step_1.pk])
        )

        self.assertNotContains(response, 'value="gateway"', html=False)
        for role in ("terminal", "satellite", "control", "watchtower"):
            self.assertContains(response, f'value="{role}"', html=False)

    @override_settings(NODE_ROLE="Constellation")
    def test_role_validation_normalizes_constellation_alias_for_commands(self):
        summary = _build_node_role_validation_summary()

        self.assertEqual(summary["configured_role"], "Watchtower")
        self.assertIn("./configure.sh --watchtower", summary["commands"])
        self.assertNotIn(
            "./configure.sh --terminal|--satellite|--control|--watchtower",
            summary["commands"],
        )

    def test_completing_all_steps_shows_completion_message_on_dashboard(self):
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_1.pk])
        )
        complete_response = self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_2.pk])
        )

        self.assertContains(complete_response, "Operator journey complete")

        dashboard_response = self.client.get(reverse("admin:index"))
        self.assertContains(
            dashboard_response,
            "All Operator tasks completed to date. Keep coming back for more.",
        )

    def test_dashboard_shows_operator_journey_for_admin_user_without_group_assignment(
        self,
    ):
        site_operator_group = SecurityGroup.objects.create(
            name=SITE_OPERATOR_GROUP_NAME
        )
        admin_user = get_user_model().objects.create_user(
            username="admin",
            password="x",
            is_staff=True,
            is_superuser=True,
        )
        admin_user.groups.clear()

        admin_journey = OperatorJourney.objects.create(
            name="Admin Journey",
            slug="admin-journey",
            security_group=site_operator_group,
            is_active=True,
        )
        OperatorJourneyStep.objects.create(
            journey=admin_journey,
            title="Run admin setup",
            slug="run-admin-setup",
            instruction="Run setup.",
            iframe_url="/admin/",
            order=1,
        )

        self.client.force_login(admin_user)
        response = self.client.get(reverse("admin:index"))

        self.assertContains(response, "Run admin setup")
        self.assertTrue(
            admin_user.groups.filter(name=SITE_OPERATOR_GROUP_NAME).exists()
        )

    def test_provision_step_renders_account_form(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_1.pk])
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_2.pk])
        )

        response = self.client.get(
            reverse("ops:operator-journey-step", args=[provision_step.pk])
        )

        self.assertContains(response, "Create account and complete step")
        self.assertNotContains(response, "<iframe", html=False)

    def test_provision_step_creates_superuser_groups_and_github_token(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_1.pk])
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_2.pk])
        )
        extra_group = SecurityGroup.objects.create(name="Provisioned Ops Group")

        response = self.client.post(
            reverse("ops:operator-journey-step-complete", args=[provision_step.pk]),
            {
                "username": "ops-provisioned",
                "email": "ops-provisioned@example.com",
                "security_groups": [self.group.pk, extra_group.pk],
                "password_mode": "random",
                "github_username": "octocat",
                "github_token": "ghp_example_token",
            },
        )

        self.assertContains(response, "Operational superuser created")
        self.assertContains(response, "Record this password securely now")
        created_user = get_user_model().objects.get(username="ops-provisioned")
        self.assertTrue(created_user.is_superuser)
        self.assertTrue(created_user.is_staff)
        self.assertSetEqual(
            set(created_user.groups.values_list("name", flat=True)),
            {self.group.name, extra_group.name},
        )
        token = GitHubToken.objects.get(user=created_user)
        self.assertEqual(token.label, "octocat")

    def test_provision_step_ignores_autofilled_password_when_mode_is_random(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_1.pk])
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_2.pk])
        )

        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[provision_step.pk]),
            {
                "username": "ops-random-password",
                "email": "ops-random-password@example.com",
                "security_groups": [self.group.pk],
                "password_mode": "random",
                "password": "autofilled-password",
            },
        )

        created_user = get_user_model().objects.get(username="ops-random-password")
        self.assertFalse(created_user.check_password("autofilled-password"))

    def test_provision_step_rejects_existing_username(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_1.pk])
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_2.pk])
        )
        get_user_model().objects.create_user(
            username="existing-ops-user",
            password="x",
            is_staff=True,
            is_superuser=True,
        )

        response = self.client.post(
            reverse("ops:operator-journey-step-complete", args=[provision_step.pk]),
            {
                "username": "existing-ops-user",
                "email": "ops-provisioned@example.com",
                "security_groups": [self.group.pk],
                "password_mode": "random",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A user with this username already exists.")

    def test_provision_step_post_rejects_when_not_current_required_step(self):
        blocked_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )

        response = self.client.post(
            reverse("ops:operator-journey-step-complete", args=[blocked_step.pk]),
            {
                "username": "ops-not-allowed",
                "email": "ops-provisioned@example.com",
                "security_groups": [self.group.pk],
                "password_mode": "random",
            },
            follow=True,
        )

        self.assertRedirects(
            response, reverse("ops:operator-journey-step", args=[self.step_1.pk])
        )
        self.assertFalse(
            get_user_model().objects.filter(username="ops-not-allowed").exists()
        )

    def test_non_superuser_staff_cannot_view_or_submit_provision_step(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_1.pk])
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_2.pk])
        )

        staff_user = get_user_model().objects.create_user(
            username="staff-operator",
            password="x",
            is_staff=True,
            is_superuser=False,
        )
        staff_user.groups.add(self.group)
        self.client.force_login(staff_user)
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_1.pk])
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", args=[self.step_2.pk])
        )

        view_response = self.client.get(
            reverse("ops:operator-journey-step", args=[provision_step.pk])
        )
        self.assertEqual(view_response.status_code, 403)

        submit_response = self.client.post(
            reverse("ops:operator-journey-step-complete", args=[provision_step.pk]),
            {
                "username": "ops-should-not-create",
                "security_groups": [self.group.pk],
                "password_mode": "random",
            },
        )
        self.assertEqual(submit_response.status_code, 403)
        self.assertFalse(
            get_user_model().objects.filter(username="ops-should-not-create").exists()
        )


class OperatorJourneyTemplateTagTests(TestCase):
    """Validate operator journey template tag fallback contexts."""

    def test_tag_returns_empty_status_without_request_context(self):
        rendered = Template(
            "{% load operator_journey %}"
            "{% operator_journey_status as operator_journey %}"
            "{{ operator_journey.task_title|default:'__empty__' }}"
        ).render(Context({}))

        self.assertEqual(rendered, "__empty__")
