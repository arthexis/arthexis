from unittest.mock import patch

from django.test import TestCase

from apps.cards.models import RFID, RFIDGeneratedLabel
from apps.cards.rfid_names import generated_label_for_rfid, rfid_name_key


class RFIDGeneratedLabelTests(TestCase):
    def test_register_scan_assigns_stable_generated_label(self):
        tag, created = RFID.register_scan("DC476D46B0", kind=RFID.CLASSIC)

        self.assertTrue(created)
        self.assertEqual(tag.name_key, "DC476D")
        self.assertEqual(tag.generated_label, generated_label_for_rfid("DC476D46B0"))
        self.assertEqual(tag.display_label, tag.generated_label)
        self.assertLessEqual(len(tag.generated_label), 16)

        same_tag, created = RFID.register_scan("DC476D46B0", kind=RFID.CLASSIC)

        self.assertFalse(created)
        self.assertEqual(same_tag.pk, tag.pk)
        self.assertEqual(same_tag.generated_label, tag.generated_label)

    def test_suffix_variant_uses_same_generated_label(self):
        first, _created = RFID.register_scan("DC476D46B0", kind=RFID.CLASSIC)
        second, _created = RFID.register_scan("DC476DAAAA", kind=RFID.CLASSIC)

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.name_key, second.name_key)
        self.assertEqual(first.generated_label, second.generated_label)
        self.assertEqual(RFIDGeneratedLabel.objects.count(), 1)

    def test_custom_label_overrides_generated_display_label(self):
        tag, _created = RFID.register_scan("AABBCCDD", kind=RFID.CLASSIC)
        tag.custom_label = "Front Desk"

        self.assertEqual(tag.display_label, "Front Desk")

    def test_collision_retry_preserves_same_key_names(self):
        first = RFID.objects.create(rfid="AAAABBBB")
        first.ensure_generated_label()

        with patch(
            "apps.cards.models.rfid.stable_rfid_label",
            side_effect=[first.generated_label, "CalmCedar123"],
        ):
            second = RFID.objects.create(rfid="CCCCDDDD")
            second.ensure_generated_label()

        self.assertEqual(second.name_key, rfid_name_key("CCCCDDDD"))
        self.assertEqual(second.generated_label, "CalmCedar123")

    def test_rfid_change_clears_stale_generated_label(self):
        tag = RFID.objects.create(rfid="AAAABBBB")
        tag.ensure_generated_label()
        original_label = tag.generated_label

        tag.rfid = "CCCCDDDD"
        tag.save(update_fields=["rfid"])
        tag.refresh_from_db(fields=["rfid", "name_key", "generated_label"])

        self.assertEqual(tag.name_key, rfid_name_key("CCCCDDDD"))
        self.assertEqual(tag.generated_label, "")

        tag.ensure_generated_label()

        self.assertEqual(tag.generated_label, generated_label_for_rfid("CCCCDDDD"))
        self.assertNotEqual(tag.generated_label, original_label)

    def test_register_scan_regenerates_label_after_canonical_rfid_adoption(self):
        tag = RFID.objects.create(rfid="B0466D47DC")
        tag.ensure_generated_label()
        original_label = tag.generated_label

        existing, created = RFID.register_scan("DC476D46B0", kind=RFID.CLASSIC)

        self.assertFalse(created)
        self.assertEqual(existing.pk, tag.pk)
        self.assertEqual(existing.rfid, "DC476D46B0")
        self.assertEqual(existing.name_key, rfid_name_key("DC476D46B0"))
        self.assertEqual(
            existing.generated_label,
            generated_label_for_rfid("DC476D46B0"),
        )
        self.assertNotEqual(existing.generated_label, original_label)

    def test_update_or_create_regenerates_label_after_canonical_rfid_adoption(self):
        tag = RFID.objects.create(rfid="B0466D47DC")
        tag.ensure_generated_label()
        original_label = tag.generated_label

        existing, created = RFID.update_or_create_from_code("DC476D46B0")

        self.assertFalse(created)
        self.assertEqual(existing.pk, tag.pk)
        self.assertEqual(existing.rfid, "DC476D46B0")
        self.assertEqual(existing.name_key, rfid_name_key("DC476D46B0"))
        self.assertEqual(
            existing.generated_label,
            generated_label_for_rfid("DC476D46B0"),
        )
        self.assertNotEqual(existing.generated_label, original_label)
