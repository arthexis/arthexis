import os
import sys
from pathlib import Path

import pytest
from tablib import Dataset

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase

from core.admin import RFIDResource
from core.models import CustomerAccount, RFID


pytestmark = [pytest.mark.feature("rfid-scanner")]


class RFIDAdminImportTests(TestCase):
    def test_import_merges_conflicting_uid(self):
        existing = RFID.objects.create(rfid="AABBCCDD", custom_label="Existing")
        account = CustomerAccount.objects.create(name="Office Access")
        original_label = existing.label_id

        dataset = Dataset(
            headers=["label_id", "rfid", "custom_label", "energy_accounts"],
        )
        dataset.append(("999", "aabbccdd", "Updated Label", str(account.pk)))

        resource = RFIDResource(account_field="id")
        result = resource.import_data(dataset, dry_run=False, raise_errors=True)

        self.assertFalse(result.has_errors())
        self.assertEqual(result.totals.get("update"), 1)
        self.assertEqual(result.totals.get("new", 0), 0)
        self.assertEqual(RFID.objects.count(), 1)

        existing.refresh_from_db()
        self.assertEqual(existing.custom_label, "Updated Label")
        self.assertEqual(existing.label_id, original_label)
        self.assertEqual(existing.rfid, "AABBCCDD")
        self.assertQuerySetEqual(
            existing.energy_accounts.values_list("pk", flat=True),
            [account.pk],
            ordered=False,
        )
