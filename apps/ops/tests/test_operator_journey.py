"""Regression tests for operator journey progression and admin dashboard surfacing."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.template import Context, Template
from django.urls import reverse

from apps.groups.constants import SITE_OPERATOR_GROUP_NAME
from apps.groups.models import SecurityGroup
from apps.nodes.models import NodeRole
from apps.ops.forms import OperatorJourneyProvisionSuperuserForm
from apps.ops.models import OperatorJourney, OperatorJourneyStep
from apps.ops.operator_journey import (
    PROVISION_SUPERUSER_STEP_SLUG,
    ROLE_VALIDATION_STEP_SLUG,
    complete_step_for_user,
    next_step_for_user,
    status_for_user,
)
from apps.ops.views import (
    _build_node_role_validation_summary,
    _build_security_group_rows,
)


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

        self.assertTrue(complete_step_for_user(user=self.user, step=self.step_1))
        status = status_for_user(user=self.user)
        self.assertEqual(status.message, "Step 2")
        self.assertEqual(status.task_title, "Step 2")

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
        self.assertIn(step_3.journey.slug, status.url)
        self.assertIn(step_3.slug, status.url)

    def test_cannot_complete_out_of_order_step(self):
        completed = complete_step_for_user(user=self.user, step=self.step_2)

        self.assertFalse(completed)

    @patch("apps.ops.operator_journey._local_node_role_is_available", return_value=True)
    def test_next_step_skips_provision_for_non_superuser_operational_staff(
        self, _mock_role_check
    ):
        role_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Validate node",
            slug=ROLE_VALIDATION_STEP_SLUG,
            instruction="Validate local node role.",
            iframe_url="/admin/",
            order=3,
        )
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create operational superuser",
            slug=PROVISION_SUPERUSER_STEP_SLUG,
            instruction="Create account.",
            iframe_url="/admin/",
            order=4,
        )
        follow_up_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Follow-up",
            slug="follow-up-step",
            instruction="Continue setup.",
            iframe_url="/admin/",
            order=5,
        )
        staff_user = get_user_model().objects.create_user(
            username="existing-operator",
            password="x",
            is_staff=True,
        )
        staff_user.groups.add(self.group)
        complete_step_for_user(user=staff_user, step=self.step_1)
        complete_step_for_user(user=staff_user, step=self.step_2)

        next_step = next_step_for_user(user=staff_user)

        self.assertEqual(next_step, follow_up_step)
        self.assertTrue(provision_step.completions.filter(user=staff_user).exists())
        self.assertTrue(role_step.completions.filter(user=staff_user).exists())

    @patch("apps.ops.operator_journey._local_node_role_is_available", return_value=True)
    def test_next_step_skips_role_validation_when_local_node_role_exists(
        self, _mock_role_check
    ):
        role_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Validate node",
            slug=ROLE_VALIDATION_STEP_SLUG,
            instruction="Validate local node role.",
            iframe_url="/admin/",
            order=3,
        )
        follow_up_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Follow-up",
            slug="follow-up-step",
            instruction="Continue setup.",
            iframe_url="/admin/",
            order=4,
        )
        complete_step_for_user(user=self.user, step=self.step_1)
        complete_step_for_user(user=self.user, step=self.step_2)

        next_step = next_step_for_user(user=self.user)

        self.assertEqual(next_step, follow_up_step)
        self.assertTrue(role_step.completions.filter(user=self.user).exists())


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

        self.assertContains(response, "Next:")
        self.assertContains(response, "Validate role")
        self.assertNotContains(response, "admin-home-operator-journey__age")
        self.assertContains(
            response,
            reverse("ops:operator-journey-step", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug}),
        )

    def test_step_view_redirects_when_opening_future_step(self):
        response = self.client.get(
            reverse("ops:operator-journey-step", kwargs={"journey_slug": self.step_2.journey.slug, "step_slug": self.step_2.slug})
        )

        self.assertRedirects(
            response, reverse("ops:operator-journey-step", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )

    def test_legacy_complete_url_resolves_to_legacy_view_for_get_requests(self):
        response = self.client.get(
            reverse(
                "ops:operator-journey-step-complete-legacy",
                kwargs={"step_id": self.step_1.pk},
            )
        )

        self.assertRedirects(
            response,
            reverse(
                "ops:operator-journey-step",
                kwargs={
                    "journey_slug": self.step_1.journey.slug,
                    "step_slug": self.step_1.slug,
                },
            ),
        )

    def test_legacy_complete_post_redirect_preserves_request_method(self):
        response = self.client.post(
            reverse(
                "ops:operator-journey-step-complete-legacy",
                kwargs={"step_id": self.step_1.pk},
            )
        )

        self.assertEqual(response.status_code, 307)
        self.assertEqual(
            response["Location"],
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": self.step_1.journey.slug,
                    "step_slug": self.step_1.slug,
                },
            ),
        )

    def test_validate_role_step_shows_setup_check_instead_of_iframe(self):
        response = self.client.get(
            reverse("ops:operator-journey-step", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
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

    def test_step_view_renders_breadcrumb_with_step_title(self):
        response = self.client.get(
            reverse("ops:operator-journey-step", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )

        self.assertContains(response, '<div class="breadcrumbs">', html=False)
        self.assertContains(response, "Operator journey")
        self.assertContains(response, self.step_1.title)

    def test_validate_role_step_limits_role_choices_to_basic_configure_roles(self):
        NodeRole.objects.create(name="Gateway")
        response = self.client.get(
            reverse("ops:operator-journey-step", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
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
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )
        complete_response = self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_2.journey.slug, "step_slug": self.step_2.slug})
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
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_2.journey.slug, "step_slug": self.step_2.slug})
        )

        response = self.client.get(
            reverse("ops:operator-journey-step", kwargs={"journey_slug": provision_step.journey.slug, "step_slug": provision_step.slug})
        )

        self.assertContains(response, "Create account and complete step")
        self.assertContains(response, "Security group")
        self.assertContains(response, "Staff")
        self.assertContains(response, "Apps")
        self.assertContains(response, "User details")
        self.assertContains(response, "id=\"nav-sidebar\"", html=False)
        self.assertNotContains(response, "<iframe", html=False)

    def test_security_group_rows_support_unbound_form_with_pk_initial_values(self):
        extra_group = SecurityGroup.objects.create(name="Initial PK group")
        provision_form = OperatorJourneyProvisionSuperuserForm()
        provision_form.fields["security_groups"].initial = [
            self.group.pk,
            str(extra_group.pk),
        ]

        security_group_rows = _build_security_group_rows(provision_form)

        selected_ids = {
            row["id"] for row in security_group_rows if row.get("selected") is True
        }
        self.assertSetEqual(selected_ids, {self.group.pk, extra_group.pk})

    def test_security_group_rows_read_selected_values_from_bound_form_data(self):
        extra_group = SecurityGroup.objects.create(name="Bound data group")
        provision_form = OperatorJourneyProvisionSuperuserForm(
            data={
                "username": "",
                "password_mode": "random",
                "security_groups": [str(extra_group.pk)],
            }
        )

        security_group_rows = _build_security_group_rows(provision_form)

        selected_ids = {
            row["id"] for row in security_group_rows if row.get("selected") is True
        }
        self.assertSetEqual(selected_ids, {extra_group.pk})

    def test_security_group_rows_include_staff_flag_and_apps(self):
        SecurityGroup.objects.create(name=SITE_OPERATOR_GROUP_NAME)
        SecurityGroup.objects.create(
            app="billing",
            name="Custom app group",
        )
        provision_form = OperatorJourneyProvisionSuperuserForm()

        security_group_rows = _build_security_group_rows(provision_form)

        rows_by_name = {row["name"]: row for row in security_group_rows}
        self.assertTrue(rows_by_name[SITE_OPERATOR_GROUP_NAME]["is_staff_group"])
        self.assertEqual(rows_by_name[SITE_OPERATOR_GROUP_NAME]["apps"], "—")
        self.assertFalse(rows_by_name["Custom app group"]["is_staff_group"])
        self.assertEqual(rows_by_name["Custom app group"]["apps"], "billing")

    def test_provision_step_creates_superuser_with_assigned_groups(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_2.journey.slug, "step_slug": self.step_2.slug})
        )
        extra_group = SecurityGroup.objects.create(name="Provisioned Ops Group")

        response = self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": provision_step.journey.slug, "step_slug": provision_step.slug}),
            {
                "username": "ops-provisioned",
                "email": "ops-provisioned@example.com",
                "security_groups": [self.group.pk, extra_group.pk],
                "password_mode": "random",
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
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_2.journey.slug, "step_slug": self.step_2.slug})
        )

        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": provision_step.journey.slug, "step_slug": provision_step.slug}),
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

    def test_provision_step_requires_upgrade_checkbox_for_existing_username(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_2.journey.slug, "step_slug": self.step_2.slug})
        )
        get_user_model().objects.create_user(
            username="existing-ops-user",
            password="x",
            is_staff=True,
            is_superuser=True,
        )

        response = self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": provision_step.journey.slug, "step_slug": provision_step.slug}),
            {
                "username": "existing-ops-user",
                "email": "ops-provisioned@example.com",
                "security_groups": [self.group.pk],
                "password_mode": "random",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upgrade existing user")

    def test_provision_step_can_upgrade_existing_username(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_2.journey.slug, "step_slug": self.step_2.slug})
        )
        existing_user = get_user_model().objects.create_user(
            username="existing-ops-user",
            email="old@example.com",
            password="old-password",
            is_active=False,
            is_staff=False,
            is_superuser=False,
        )
        old_group = SecurityGroup.objects.create(name="Old upgrade group")
        existing_user.groups.add(old_group)
        if hasattr(existing_user, "is_deleted"):
            existing_user.is_deleted = True
            existing_user.save(update_fields=["is_deleted"])

        response = self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": provision_step.journey.slug, "step_slug": provision_step.slug}),
            {
                "username": "existing-ops-user",
                "email": "ops-provisioned@example.com",
                "security_groups": [self.group.pk],
                "password_mode": "custom",
                "password": "new-secure-password",
                "upgrade_existing_user": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        existing_user.refresh_from_db()
        self.assertTrue(existing_user.is_staff)
        self.assertTrue(existing_user.is_superuser)
        self.assertTrue(existing_user.is_active)
        if hasattr(existing_user, "is_deleted"):
            self.assertFalse(existing_user.is_deleted)
        self.assertEqual(existing_user.email, "ops-provisioned@example.com")
        self.assertTrue(existing_user.check_password("new-secure-password"))
        self.assertSetEqual(
            set(existing_user.groups.values_list("pk", flat=True)),
            {self.group.pk},
        )
        self.assertContains(response, "Operational superuser upgraded")

    @override_settings(
        AUTH_PASSWORD_VALIDATORS=[
            {
                "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
                "OPTIONS": {"min_length": 16},
            }
        ]
    )
    def test_provision_step_rejects_custom_password_that_fails_validators(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_2.journey.slug, "step_slug": self.step_2.slug})
        )

        response = self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": provision_step.journey.slug, "step_slug": provision_step.slug}),
            {
                "username": "ops-provisioned-weak-password",
                "email": "ops-provisioned-weak-password@example.com",
                "security_groups": [self.group.pk],
                "password_mode": "custom",
                "password": "too-short",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "This password is too short. It must contain at least 16 characters.",
        )
        self.assertFalse(
            get_user_model()
            .objects.filter(username="ops-provisioned-weak-password")
            .exists()
        )

    @override_settings(
        AUTH_PASSWORD_VALIDATORS=[
            {
                "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
            }
        ]
    )
    def test_provision_step_upgrade_validates_password_against_submitted_email(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_2.journey.slug, "step_slug": self.step_2.slug})
        )
        existing_user = get_user_model().objects.create_user(
            username="existing-ops-user-email-change",
            email="old-upgrade-email@example.com",
            password="old-password",
            is_active=False,
            is_staff=False,
            is_superuser=False,
        )

        response = self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": provision_step.journey.slug, "step_slug": provision_step.slug}),
            {
                "username": existing_user.username,
                "email": "new-upgrade-email@example.com",
                "security_groups": [self.group.pk],
                "password_mode": "custom",
                "password": "new-upgrade-email@example.com",
                "upgrade_existing_user": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "The password is too similar to the email address.",
        )
        existing_user.refresh_from_db()
        self.assertEqual(existing_user.email, "old-upgrade-email@example.com")
        self.assertFalse(existing_user.check_password("new-upgrade-email@example.com"))

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
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": blocked_step.journey.slug, "step_slug": blocked_step.slug}),
            {
                "username": "ops-not-allowed",
                "email": "ops-provisioned@example.com",
                "security_groups": [self.group.pk],
                "password_mode": "random",
            },
            follow=True,
        )

        self.assertRedirects(
            response, reverse("ops:operator-journey-step", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )
        self.assertFalse(
            get_user_model().objects.filter(username="ops-not-allowed").exists()
        )

    def test_non_superuser_staff_auto_completes_provision_step(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_2.journey.slug, "step_slug": self.step_2.slug})
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
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_1.journey.slug, "step_slug": self.step_1.slug})
        )
        self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": self.step_2.journey.slug, "step_slug": self.step_2.slug})
        )

        view_response = self.client.get(
            reverse("ops:operator-journey-step", kwargs={"journey_slug": provision_step.journey.slug, "step_slug": provision_step.slug})
        )
        self.assertEqual(view_response.status_code, 200)
        self.assertContains(view_response, "Operator journey complete")

        submit_response = self.client.post(
            reverse("ops:operator-journey-step-complete", kwargs={"journey_slug": provision_step.journey.slug, "step_slug": provision_step.slug}),
            {
                "username": "ops-should-not-create",
                "security_groups": [self.group.pk],
                "password_mode": "random",
            },
        )
        self.assertEqual(submit_response.status_code, 302)
        self.assertRedirects(submit_response, reverse("admin:index"))
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
