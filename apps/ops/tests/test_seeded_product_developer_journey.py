"""Regression tests for seeded product developer GitHub journey defaults."""

from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from apps.groups.models import SecurityGroup
from apps.ops.models import OperatorJourney, OperatorJourneyStep

migration_module = import_module(
    "apps.ops.migrations.0010_split_github_setup_into_product_developer_journey"
)


class SeededProductDeveloperJourneyTests(TestCase):
    """Ensure GitHub setup guidance is split from the Site Operator superuser step."""

    def test_split_github_setup_into_product_developer_journey(self):
        site_operator = SecurityGroup.objects.create(name="Site Operator")
        operator_journey = OperatorJourney.objects.create(
            name="Operator Node Readiness",
            slug="operator-node-readiness",
            security_group=site_operator,
            is_active=True,
            is_seed_data=True,
        )
        OperatorJourneyStep.objects.create(
            journey=operator_journey,
            title="Create operational superuser and security access",
            slug="provision-ops-superuser",
            instruction="Old instruction with GitHub guidance.",
            help_text="Old help text with GitHub guidance.",
            iframe_url="/admin/auth/user/add/",
            order=2,
            is_active=True,
            is_seed_data=True,
        )

        migration_module.split_github_setup_into_product_developer_journey(
            django_apps, None
        )

        refreshed_step = OperatorJourneyStep.objects.get(
            journey=operator_journey,
            slug="provision-ops-superuser",
        )
        self.assertNotIn("GitHub", refreshed_step.instruction)

        developer_journey = OperatorJourney.objects.get(
            slug="product-developer-github-access"
        )
        self.assertEqual(developer_journey.security_group.name, "Product Developer")

        github_step = OperatorJourneyStep.objects.get(
            journey=developer_journey,
            slug="setup-github-token",
        )
        self.assertEqual(github_step.iframe_url, "/admin/repos/githubrepository/setup-token/")
        self.assertIn("GitHub token setup wizard", github_step.instruction)
