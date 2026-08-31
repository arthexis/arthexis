from django.test import SimpleTestCase, TestCase

from apps.cards.models import RFID
from apps.cards.rfid_names import (
    generated_label_for_rfid,
    rfid_name_key,
    stable_rfid_label,
)


class RFIDNameTests(SimpleTestCase):
    def test_rfid_name_key_drops_charger_ignored_suffix(self):
        self.assertEqual(rfid_name_key("dc47 6d46 b0"), "DC476D")

    def test_rfid_name_key_keeps_short_values_nonempty(self):
        self.assertEqual(rfid_name_key("cafe"), "CAFE")

    def test_generated_label_is_stable_human_readable_and_short(self):
        first = generated_label_for_rfid("DC476D46B0")
        second = generated_label_for_rfid("DC476D1234")

        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 16)
        self.assertTrue(first[-3:].isdigit())
        self.assertTrue(first[:-3].isalpha())

    def test_collision_counter_changes_candidate(self):
        name_key = rfid_name_key("DC476D46B0")

        self.assertNotEqual(
            stable_rfid_label(name_key, counter=0),
            stable_rfid_label(name_key, counter=1),
        )

    def test_rfid_normalize_code_accepts_separated_uid_bytes(self):
        self.assertEqual(RFID.normalize_code("32:9b:f1:72:2a"), "329BF1722A")
        self.assertEqual(RFID.normalize_code("32-9b-f1-72-2a"), "329BF1722A")
        self.assertEqual(RFID.normalize_code("32 9b f1 72 2a"), "329BF1722A")

    def test_rfid_normalize_code_preserves_non_hex_ocpp_id_tags(self):
        self.assertEqual(RFID.normalize_code("EVREADY-001"), "EVREADY-001")

    def test_rfid_validator_accepts_ocpp_identifiers_and_preserves_separators(self):
        RFID(rfid="EVREADY-001").full_clean(validate_unique=False)
        RFID(rfid="AB-12").full_clean(validate_unique=False)
        self.assertEqual(RFID.normalize_code("AB-12"), "AB-12")
        self.assertEqual(RFID.normalize_code("AB12"), "AB12")


class RFIDPersistenceTests(TestCase):
    def test_partial_save_persists_normalized_rfid_and_derived_fields(self):
        tag = RFID.objects.create(rfid="329BF1722A")
        tag.rfid = "32:9b:f1:72:2a"
        tag.custom_label = "EV Ready"

        tag.save(update_fields=["custom_label"])
        tag.refresh_from_db()

        self.assertEqual(tag.rfid, "329BF1722A")
        self.assertEqual(tag.name_key, "329BF1")
        self.assertEqual(tag.reversed_uid, "2A72F19B32")
