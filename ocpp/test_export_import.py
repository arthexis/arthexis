import json
import os
import tempfile
from datetime import timedelta
import io

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from django.contrib.auth import get_user_model

from ocpp.models import Charger, Transaction, MeterValue
from core.models import CustomerAccount


class TransactionExportImportTests(TestCase):
    def setUp(self):
        self.account = CustomerAccount.objects.create(name="ACC")
        self.ch1 = Charger.objects.create(charger_id="C1")
        self.ch2 = Charger.objects.create(charger_id="C2")
        now = timezone.now()
        self.tx_old = Transaction.objects.create(
            charger=self.ch1,
            account=self.account,
            start_time=now - timedelta(days=5),
        )
        self.tx_new = Transaction.objects.create(
            charger=self.ch2,
            start_time=now,
        )
        MeterValue.objects.create(
            charger=self.ch1,
            transaction=self.tx_old,
            timestamp=now - timedelta(days=5),
            energy=1,
        )

    def test_export_filters_and_import_creates_chargers(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        start = (timezone.now() - timedelta(days=1)).date().isoformat()
        call_command(
            "export_transactions",
            tmp_path,
            "--start",
            start,
            "--chargers",
            self.ch2.charger_id,
        )
        with open(tmp_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(len(data["transactions"]), 1)
        self.assertEqual(data["transactions"][0]["charger"], "C2")

        MeterValue.objects.all().delete()
        Transaction.objects.all().delete()
        Charger.objects.all().delete()

        call_command("import_transactions", tmp_path)
        os.remove(tmp_path)

        self.assertTrue(Charger.objects.filter(charger_id="C2").exists())
        self.assertEqual(Transaction.objects.count(), 1)
        tx = Transaction.objects.first()
        self.assertEqual(tx.charger.charger_id, "C2")


class TransactionAdminExportImportTests(TestCase):
    def setUp(self):
        self.account = CustomerAccount.objects.create(name="ACC")
        self.ch1 = Charger.objects.create(charger_id="C1")
        self.ch2 = Charger.objects.create(charger_id="C2")
        now = timezone.now()
        Transaction.objects.create(
            charger=self.ch1,
            account=self.account,
            start_time=now - timedelta(days=2),
        )
        Transaction.objects.create(
            charger=self.ch2,
            start_time=now,
        )
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="txadmin", email="txadmin@example.com", password="pwd"
        )
        self.client.force_login(self.admin)

    def test_admin_export_filters(self):
        url = reverse("admin:ocpp_transaction_export")
        start = (timezone.now() - timedelta(days=1)).isoformat()
        response = self.client.post(
            url,
            {"start": start, "chargers": [self.ch2.pk]},
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(len(data["transactions"]), 1)
        self.assertEqual(data["transactions"][0]["charger"], "C2")

    def test_admin_import_creates_charger(self):
        url = reverse("admin:ocpp_transaction_import")
        payload = {
            "chargers": [
                {"charger_id": "C9", "connector_id": 1, "require_rfid": False}
            ],
            "transactions": [
                {
                    "charger": "C9",
                    "account": None,
                    "rfid": "",
                    "vid": "",
                    "vin": "",
                    "meter_start": 0,
                    "meter_stop": 0,
                    "start_time": timezone.now().isoformat(),
                    "stop_time": None,
                    "meter_values": [],
                }
            ],
        }
        json_file = io.StringIO(json.dumps(payload))
        json_file.name = "tx.json"
        response = self.client.post(url, {"file": json_file})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Charger.objects.filter(charger_id="C9").exists())
        self.assertEqual(
            Transaction.objects.filter(charger__charger_id="C9").count(), 1
        )
