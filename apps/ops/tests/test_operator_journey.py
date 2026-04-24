"""Regression tests for operator journey progression and admin dashboard surfacing."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.template import Context, Template
from django.urls import reverse

from apps.groups.constants import SITE_OPERATOR_GROUP_NAME
from apps.groups.models import SecurityGroup
from apps.nodes.models import NodeRole
from apps.ops.forms import (
    OperatorJourneyGitHubAccessForm,
    OperatorJourneyProvisionSuperuserForm,
)
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

    def test_product_developer_follow_up_steps_are_ordered_after_token_setup(self):
        github_journey = OperatorJourney.objects.create(
            name="Product Developer GitHub Access",
            slug="product-developer-github-access",
            security_group=self.group,
            is_active=True,
            priority=1,
        )
        setup_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Connect your GitHub access",
            slug="setup-github-token",
            instruction="Configure GitHub access directly in this step.",
            iframe_url="/admin/repos/githubrepository/setup-token/",
            order=1,
        )
        issue_inbox_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Review GitHub issue inbox",
            slug="review-issue-inbox",
            instruction="Triage open repository issues.",
            iframe_url="/admin/repos/repositoryissue/?state__exact=open",
            order=2,
        )
        pr_queue_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Review pull request queue",
            slug="review-pr-queue",
            instruction="Review open pull requests.",
            iframe_url="/admin/repos/repositorypullrequest/?state__exact=open",
            order=3,
        )
        issue_lifecycle_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Execute issue lifecycle actions",
            slug="run-issue-lifecycle-actions",
            instruction="Perform issue follow-up and closure actions.",
            iframe_url="/admin/repos/repositoryissue/",
            order=4,
        )
        pr_lifecycle_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Execute pull request lifecycle actions",
            slug="run-pr-lifecycle-actions",
            instruction="Perform pull request review and completion actions.",
            iframe_url="/admin/repos/repositorypullrequest/",
            order=5,
        )

        step_titles_in_order = list(
            OperatorJourneyStep.objects.filter(journey=github_journey).values_list(
                "title", flat=True
            )
        )

        self.assertEqual(
            step_titles_in_order,
            [
                setup_step.title,
                issue_inbox_step.title,
                pr_queue_step.title,
                issue_lifecycle_step.title,
                pr_lifecycle_step.title,
            ],
        )

    def test_product_developer_progression_advances_after_token_setup(self):
        github_journey = OperatorJourney.objects.create(
            name="Product Developer GitHub Access",
            slug="product-developer-github-access",
            security_group=self.group,
            is_active=True,
            priority=1,
        )
        setup_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Connect your GitHub access",
            slug="setup-github-token",
            instruction="Configure GitHub access directly in this step.",
            iframe_url="/admin/repos/githubrepository/setup-token/",
            order=1,
        )
        issue_inbox_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Review GitHub issue inbox",
            slug="review-issue-inbox",
            instruction="Triage open repository issues.",
            iframe_url="/admin/repos/repositoryissue/?state__exact=open",
            order=2,
        )

        self.assertEqual(next_step_for_user(user=self.user), setup_step)

        self.assertTrue(complete_step_for_user(user=self.user, step=setup_step))
        self.assertEqual(next_step_for_user(user=self.user), issue_inbox_step)

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

    def test_step_view_redirects_when_opening_future_step(self):
        response = self.client.get(
            reverse(
                "ops:operator-journey-step",
                kwargs={
                    "journey_slug": self.step_2.journey.slug,
                    "step_slug": self.step_2.slug,
                },
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

    @patch("apps.ops.forms.github_service.validate_token")
    def test_setup_github_token_complete_saves_after_successful_validation(
        self, mock_validate_token
    ):
        mock_validate_token.return_value = (
            True,
            "Connected to GitHub as arthexis.",
            "arthexis",
        )
        GitHubToken.objects.create(user=self.user, label="old", token="ghp_demo_token")
        complete_step_for_user(user=self.user, step=self.step_1)
        complete_step_for_user(user=self.user, step=self.step_2)
        github_journey = OperatorJourney.objects.create(
            name="Product Developer GitHub Access",
            slug="product-developer-github-access",
            security_group=self.group,
            is_active=True,
            priority=1,
        )
        github_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Connect your GitHub access",
            slug="setup-github-token",
            instruction="Configure GitHub access directly in this step.",
            iframe_url="/admin/repos/githubrepository/setup-token/",
            order=1,
        )

        response = self.client.post(
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": github_journey.slug,
                    "step_slug": github_step.slug,
                },
            ),
            {
                "journey_action": "complete",
            },
        )

        self.assertEqual(response.status_code, 200)
        saved_token = GitHubToken.objects.get(user=self.user)
        self.assertEqual(saved_token.label, "arthexis")
        self.assertEqual(saved_token.token, "ghp_demo_token")
        self.assertTrue(github_step.completions.filter(user=self.user).exists())

    @patch("apps.ops.forms.github_service.validate_token")
    def test_setup_github_token_complete_allows_add_only_permissions(
        self, mock_validate_token
    ):
        add_only_user = get_user_model().objects.create_user(
            username="ops-journey-add-only",
            password="x",
            is_staff=True,
        )
        add_only_user.groups.add(self.group)
        add_only_user.user_permissions.add(
            Permission.objects.get(codename="add_githubtoken")
        )
        self.client.force_login(add_only_user)
        complete_step_for_user(user=add_only_user, step=self.step_1)
        complete_step_for_user(user=add_only_user, step=self.step_2)
        github_journey = OperatorJourney.objects.create(
            name="Product Developer GitHub Access",
            slug="product-developer-github-access",
            security_group=self.group,
            is_active=True,
            priority=1,
        )
        github_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Connect your GitHub access",
            slug="setup-github-token",
            instruction="Configure GitHub access directly in this step.",
            iframe_url="/admin/repos/githubrepository/setup-token/",
            order=1,
        )
        GitHubToken.objects.create(
            user=add_only_user,
            label="existing-label",
            token="[ENV.GITHUB_TOKEN]",
        )
        mock_validate_token.return_value = (
            True,
            "Connected to GitHub as arthexis.",
            "arthexis",
        )

        response = self.client.post(
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": github_journey.slug,
                    "step_slug": github_step.slug,
                },
            ),
            {"journey_action": "complete"},
        )

        self.assertEqual(response.status_code, 200)
        saved_token = GitHubToken.objects.get(user=add_only_user)
        self.assertEqual(saved_token.label, "existing-label")
        self.assertEqual(saved_token.__dict__["token"], "[ENV.GITHUB_TOKEN]")
        self.assertTrue(github_step.completions.filter(user=add_only_user).exists())

    @patch("apps.ops.forms.github_service.validate_token")
    @patch("apps.ops.forms.resolve_sigils")
    def test_setup_github_token_validation_uses_stored_token(
        self,
        mock_resolve_sigils,
        mock_validate_token,
    ):
        GitHubToken.objects.create(
            user=self.user,
            label="Sigil token",
            token="[ENV.GITHUB_TOKEN]",
        )
        mock_resolve_sigils.return_value = "resolved-token"
        mock_validate_token.return_value = (
            True,
            "Connected to GitHub as arthexis.",
            "arthexis",
        )
        form = OperatorJourneyGitHubAccessForm(user=self.user)
        is_valid, message, login = form.validate_connection()

        self.assertTrue(is_valid)
        self.assertEqual(message, "Connected to GitHub as arthexis.")
        self.assertEqual(login, "arthexis")
        mock_resolve_sigils.assert_called_once_with("[ENV.GITHUB_TOKEN]")
        mock_validate_token.assert_called_once_with("resolved-token")

    @override_settings(
        GITHUB_OAUTH_CLIENT_ID="client-id",
        GITHUB_OAUTH_CLIENT_SECRET="client-secret",
    )
    def test_setup_github_token_login_redirects_to_github_authorize(self):
        complete_step_for_user(user=self.user, step=self.step_1)
        complete_step_for_user(user=self.user, step=self.step_2)
        github_journey = OperatorJourney.objects.create(
            name="Product Developer GitHub Access",
            slug="product-developer-github-access",
            security_group=self.group,
            is_active=True,
            priority=1,
        )
        github_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Connect your GitHub access",
            slug="setup-github-token",
            instruction="Configure GitHub access directly in this step.",
            iframe_url="/admin/repos/githubrepository/setup-token/",
            order=1,
        )

        response = self.client.get(
            reverse(
                "ops:operator-journey-github-login",
                kwargs={
                    "journey_slug": github_journey.slug,
                    "step_slug": github_step.slug,
                },
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("github.com/login/oauth/authorize", response["Location"])
        self.assertIn("client_id=client-id", response["Location"])

    @override_settings(
        GITHUB_OAUTH_CLIENT_ID="client-id",
        GITHUB_OAUTH_CLIENT_SECRET="client-secret",
    )
    @patch("apps.ops.views.github_service.validate_token")
    @patch("apps.ops.views.requests.post")
    def test_setup_github_token_callback_saves_username_as_label(
        self,
        mock_post,
        mock_validate_token,
    ):
        mock_validate_token.return_value = (
            True,
            "Connected to GitHub as arthexis.",
            "arthexis",
        )
        mock_post.return_value.json.return_value = {"access_token": "oauth-token"}
        complete_step_for_user(user=self.user, step=self.step_1)
        complete_step_for_user(user=self.user, step=self.step_2)
        github_journey = OperatorJourney.objects.create(
            name="Product Developer GitHub Access",
            slug="product-developer-github-access",
            security_group=self.group,
            is_active=True,
            priority=1,
        )
        github_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Connect your GitHub access",
            slug="setup-github-token",
            instruction="Configure GitHub access directly in this step.",
            iframe_url="/admin/repos/githubrepository/setup-token/",
            order=1,
        )
        login_response = self.client.get(
            reverse(
                "ops:operator-journey-github-login",
                kwargs={
                    "journey_slug": github_journey.slug,
                    "step_slug": github_step.slug,
                },
            )
        )
        self.assertEqual(login_response.status_code, 302)
        state = self.client.session["ops_github_oauth_state"]["state"]

        callback_response = self.client.get(
            reverse(
                "ops:operator-journey-github-callback",
                kwargs={
                    "journey_slug": github_journey.slug,
                    "step_slug": github_step.slug,
                },
            ),
            {"code": "oauth-code", "state": state},
        )

        self.assertEqual(callback_response.status_code, 302)
        saved_token = GitHubToken.objects.get(user=self.user)
        self.assertEqual(saved_token.token, "oauth-token")
        self.assertEqual(saved_token.label, "arthexis")

    @patch("apps.ops.views.requests.post")
    def test_setup_github_token_callback_rejects_non_current_step(
        self,
        mock_post,
    ):
        locked_journey = OperatorJourney.objects.create(
            name="Locked First Step",
            slug="locked-first-step",
            security_group=self.group,
            is_active=True,
            priority=0,
        )
        OperatorJourneyStep.objects.create(
            journey=locked_journey,
            title="Locked setup",
            slug="locked-step",
            instruction="This step must be completed first.",
            iframe_url="/admin/",
            order=1,
        )
        github_journey = OperatorJourney.objects.create(
            name="Product Developer GitHub Access",
            slug="product-developer-github-access",
            security_group=self.group,
            is_active=True,
            priority=1,
        )
        github_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Connect your GitHub access",
            slug="setup-github-token",
            instruction="Configure GitHub access directly in this step.",
            iframe_url="/admin/repos/githubrepository/setup-token/",
            order=1,
        )
        session = self.client.session
        session["ops_github_oauth_state"] = {
            "journey_slug": github_journey.slug,
            "step_slug": github_step.slug,
            "state": "expected-state",
        }
        session.save()

        callback_response = self.client.get(
            reverse(
                "ops:operator-journey-github-callback",
                kwargs={
                    "journey_slug": github_journey.slug,
                    "step_slug": github_step.slug,
                },
            ),
            {"code": "oauth-code", "state": "expected-state"},
        )

        self.assertEqual(callback_response.status_code, 302)
        mock_post.assert_not_called()
        self.assertFalse(GitHubToken.objects.filter(user=self.user).exists())

    @patch("apps.ops.forms.github_service.validate_token")
    @patch("apps.ops.forms.resolve_sigils")
    def test_setup_github_token_complete_preserves_raw_sigil_token(
        self,
        mock_resolve_sigils,
        mock_validate_token,
    ):
        mock_resolve_sigils.return_value = "resolved-token"
        mock_validate_token.return_value = (
            True,
            "Connected to GitHub as arthexis.",
            "arthexis",
        )
        GitHubToken.objects.create(
            user=self.user, label="old", token="[ENV.GITHUB_TOKEN]"
        )
        complete_step_for_user(user=self.user, step=self.step_1)
        complete_step_for_user(user=self.user, step=self.step_2)
        github_journey = OperatorJourney.objects.create(
            name="Product Developer GitHub Access",
            slug="product-developer-github-access",
            security_group=self.group,
            is_active=True,
            priority=1,
        )
        github_step = OperatorJourneyStep.objects.create(
            journey=github_journey,
            title="Connect your GitHub access",
            slug="setup-github-token",
            instruction="Configure GitHub access directly in this step.",
            iframe_url="/admin/repos/githubrepository/setup-token/",
            order=1,
        )

        response = self.client.post(
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": github_journey.slug,
                    "step_slug": github_step.slug,
                },
            ),
            {"journey_action": "complete"},
        )

        self.assertEqual(response.status_code, 200)
        saved_token = GitHubToken.objects.get(user=self.user)
        self.assertEqual(saved_token.label, "arthexis")
        self.assertEqual(saved_token.__dict__["token"], "[ENV.GITHUB_TOKEN]")

    def test_legacy_complete_post_ignores_slug_collision_fast_path(self):
        collision_journey = OperatorJourney.objects.create(
            name="Collision Journey",
            slug=str(self.step_1.pk),
            security_group=self.group,
            is_active=True,
            priority=0,
        )
        OperatorJourneyStep.objects.create(
            journey=collision_journey,
            title="Collision Complete Step",
            slug="complete",
            instruction="Collision step should not consume legacy post completion.",
            iframe_url="/admin/",
            order=1,
        )

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

    @override_settings(NODE_ROLE="Constellation")
    def test_role_validation_normalizes_constellation_alias_for_commands(self):
        summary = _build_node_role_validation_summary()

        self.assertEqual(summary["configured_role"], "Watchtower")
        self.assertIn("./configure.sh --watchtower", summary["commands"])
        self.assertNotIn(
            "./configure.sh --terminal|--satellite|--control|--watchtower",
            summary["commands"],
        )

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
        self.assertEqual(
            rows_by_name["Custom app group"]["name_label"],
            "Custom app group",
        )

    def test_security_group_rows_show_name_fallback_for_blank_names(self):
        SecurityGroup.objects.create(name="")
        provision_form = OperatorJourneyProvisionSuperuserForm()

        security_group_rows = _build_security_group_rows(provision_form)

        self.assertIn(
            "Unnamed security group",
            {row["name_label"] for row in security_group_rows},
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
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": self.step_1.journey.slug,
                    "step_slug": self.step_1.slug,
                },
            )
        )
        self.client.post(
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": self.step_2.journey.slug,
                    "step_slug": self.step_2.slug,
                },
            )
        )

        self.client.post(
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": provision_step.journey.slug,
                    "step_slug": provision_step.slug,
                },
            ),
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
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": blocked_step.journey.slug,
                    "step_slug": blocked_step.slug,
                },
            ),
            {
                "username": "ops-not-allowed",
                "email": "ops-provisioned@example.com",
                "security_groups": [self.group.pk],
                "password_mode": "random",
            },
            follow=True,
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
        self.assertFalse(
            get_user_model().objects.filter(username="ops-not-allowed").exists()
        )

    def test_provision_step_allows_skip_without_creating_user(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        follow_up_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Continue setup",
            slug="continue-setup",
            instruction="Continue setup.",
            iframe_url="/admin/",
            order=4,
        )
        self.client.post(
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": self.step_1.journey.slug,
                    "step_slug": self.step_1.slug,
                },
            )
        )
        self.client.post(
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": self.step_2.journey.slug,
                    "step_slug": self.step_2.slug,
                },
            )
        )

        response = self.client.post(
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": provision_step.journey.slug,
                    "step_slug": provision_step.slug,
                },
            ),
            {"journey_action": "skip"},
        )

        self.assertRedirects(
            response,
            reverse(
                "ops:operator-journey-step",
                kwargs={
                    "journey_slug": follow_up_step.journey.slug,
                    "step_slug": follow_up_step.slug,
                },
            ),
        )
        self.assertTrue(provision_step.completions.filter(user=self.user).exists())
        self.assertFalse(
            get_user_model().objects.filter(username="ops-skip-account").exists()
        )

    def test_provision_step_view_includes_skip_button(self):
        provision_step = OperatorJourneyStep.objects.create(
            journey=self.journey,
            title="Create ops superuser",
            slug="provision-ops-superuser",
            instruction="Create account.",
            iframe_url="/admin/",
            order=3,
        )
        self.client.post(
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": self.step_1.journey.slug,
                    "step_slug": self.step_1.slug,
                },
            )
        )
        self.client.post(
            reverse(
                "ops:operator-journey-step-complete",
                kwargs={
                    "journey_slug": self.step_2.journey.slug,
                    "step_slug": self.step_2.slug,
                },
            )
        )

        response = self.client.get(
            reverse(
                "ops:operator-journey-step",
                kwargs={
                    "journey_slug": provision_step.journey.slug,
                    "step_slug": provision_step.slug,
                },
            )
        )

        self.assertContains(response, "Skip this step and continue")

    def test_tag_returns_empty_status_without_request_context(self):
        rendered = Template(
            "{% load operator_journey %}"
            "{% operator_journey_status as operator_journey %}"
            "{{ operator_journey.task_title|default:'__empty__' }}"
        ).render(Context({}))

        self.assertEqual(rendered, "__empty__")
