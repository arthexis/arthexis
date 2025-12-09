from django.contrib.contenttypes.models import ContentType
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from apps.cards.models import RFID
from apps.counters import badge_values
from apps.counters.defaults import ensure_default_badge_counters
from apps.counters.models import BadgeCounter
from apps.nodes.models import Node
from apps.ocpp.models.charger import Charger


class BadgeValueTests(TestCase):
    def test_node_known_count_reports_nodes(self):
        Node.objects.create(hostname="alpha")
        Node.objects.create(hostname="beta")

        result = badge_values.node_known_count()

        self.assertEqual(result.primary, 2)
        self.assertIn("nodes known", result.label)

    def test_rfid_release_stats_includes_released_and_total(self):
        RFID.objects.create(rfid="AA")
        RFID.objects.create(rfid="BB", released=True)

        result = badge_values.rfid_release_stats()

        self.assertEqual(result.primary, 1)
        self.assertEqual(result.secondary, 2)
        self.assertIn("released RFIDs", result.label)

    def test_charger_availability_stats_counts_missing_connectors(self):
        with mock.patch.object(Charger, "get_absolute_url", return_value="/chargers/"):
            Charger.objects.create(
                charger_id="A", connector_id=1, last_status="Available"
            )
            Charger.objects.create(
                charger_id="B", connector_id=None, last_status="Available"
            )

        result = badge_values.charger_availability_stats()

        self.assertEqual(result.primary, 1)
        self.assertEqual(result.secondary, 2)
        self.assertIn("Available status with a CP number", result.label)

    def test_badge_counter_count_reflects_current_total(self):
        starting = BadgeCounter.objects.count()

        result = badge_values.badge_counter_count()
        self.assertEqual(result.primary, starting)
        self.assertIn(str(starting), result.label)

        BadgeCounter.objects.create(
            content_type=ContentType.objects.get_for_model(Node),
            name="Extra",
            primary_source_type=BadgeCounter.ValueSource.CALLABLE,
            primary_source="apps.counters.badge_values.node_known_count",
        )

        updated_result = badge_values.badge_counter_count()
        self.assertEqual(updated_result.primary, starting + 1)

    def test_open_lead_count_returns_none_for_non_lead_models(self):
        badge = BadgeCounter(
            content_type=ContentType.objects.get_for_model(Node),
            name="Nodes",
            primary_source_type=BadgeCounter.ValueSource.CALLABLE,
            primary_source="apps.counters.badge_values.node_known_count",
        )

        self.assertIsNone(badge_values.open_lead_count(badge))


class SeedBadgeCounterTests(TestCase):
    def setUp(self):
        ensure_default_badge_counters()

    def test_seed_badge_counters_exist_with_expected_settings(self):
        node_ct = ContentType.objects.get(app_label="nodes", model="node")
        cards_ct = ContentType.objects.get(app_label="cards", model="rfid")
        counters_ct = ContentType.objects.get(app_label="counters", model="badgecounter")

        node_badge = BadgeCounter.objects.get(content_type=node_ct, name="Nodes")
        rfid_badge = BadgeCounter.objects.get(content_type=cards_ct, name="RFIDs")
        counter_badge = BadgeCounter.objects.get(
            content_type=counters_ct, name="Badge Counters"
        )

        self.assertTrue(node_badge.is_seed_data)
        self.assertTrue(rfid_badge.is_seed_data)
        self.assertTrue(counter_badge.is_seed_data)

        self.assertEqual(
            node_badge.primary_source, "apps.counters.badge_values.node_known_count"
        )
        self.assertEqual(
            rfid_badge.primary_source, "apps.counters.badge_values.rfid_release_stats"
        )
        self.assertEqual(
            counter_badge.primary_source,
            "apps.counters.badge_values.badge_counter_count",
        )

    def test_seed_badge_counters_render_current_values(self):
        node_ct = ContentType.objects.get(app_label="nodes", model="node")
        cards_ct = ContentType.objects.get(app_label="cards", model="rfid")
        counters_ct = ContentType.objects.get(app_label="counters", model="badgecounter")

        Node.objects.create(hostname="gamma")
        RFID.objects.create(rfid="CC", released=True)

        BadgeCounter.invalidate_model_cache(node_ct)
        BadgeCounter.invalidate_model_cache(cards_ct)
        BadgeCounter.invalidate_model_cache(counters_ct)

        node_badge = BadgeCounter.objects.get(content_type=node_ct, name="Nodes")
        rfid_badge = BadgeCounter.objects.get(content_type=cards_ct, name="RFIDs")
        counter_badge = BadgeCounter.objects.get(
            content_type=counters_ct, name="Badge Counters"
        )

        node_display = node_badge.build_display()
        rfid_display = rfid_badge.build_display()
        counter_display = counter_badge.build_display()

        self.assertEqual(node_display["primary"], str(Node.objects.count()))
        self.assertEqual(rfid_display["primary"], "1")
        self.assertEqual(counter_display["primary"], str(BadgeCounter.objects.count()))
