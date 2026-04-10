"""Regression tests for seeded operator journey ownership defaults."""

from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from apps.groups.models import SecurityGroup
from apps.ops.models import OperatorJourney

migration_module = import_module(
    "apps.ops.migrations.0007_retarget_seeded_operator_journey_to_site_operator"
)


class SeededOperatorJourneyTests(TestCase):
    """Ensure seeded operator journey ownership retargets to Site Operator."""

    def test_retarget_seeded_operator_journey_to_site_operator(self):
        network_operator_group = SecurityGroup.objects.create(name="Network Operator")
        OperatorJourney.objects.create(
            name="Operator Node Readiness",
            slug="operator-node-readiness",
            security_group=network_operator_group,
            is_active=True,
            is_seed_data=True,
        )

        migration_module.retarget_seeded_journey_to_site_operator(django_apps, None)

        journey = OperatorJourney.objects.get(slug="operator-node-readiness")
        self.assertEqual(journey.security_group.name, "Site Operator")
