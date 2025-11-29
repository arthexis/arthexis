import os
import sys
from pathlib import Path

import pytest


sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django


django.setup()

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomerAccount
from core.models import RFID
from ocpp.models import Transaction


pytestmark = [pytest.mark.feature("rfid-scanner")]


class RFIDAdminCreateAccountTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="rfidadmin", email="admin@example.com", password="password"
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse("admin:core_rfid_changelist")

    def test_create_account_assigns_transactions(self):
        tag = RFID.objects.create(rfid="CREATE01", custom_label="Badge 01")
        Transaction.objects.create(
            rfid=tag.rfid,
            meter_start=0,
            meter_stop=1000,
            start_time=timezone.now(),
            stop_time=timezone.now(),
        )
        Transaction.objects.create(
            rfid=tag.rfid,
            meter_start=0,
            meter_stop=2000,
            start_time=timezone.now(),
            stop_time=timezone.now(),
        )

        response = self.client.post(
            self.url,
            data={
                "action": "create_account_from_rfid",
                ACTION_CHECKBOX_NAME: [str(tag.pk)],
            },
        )

        self.assertEqual(response.status_code, 302)
        account = CustomerAccount.objects.get()
        tag.refresh_from_db()

        self.assertIn(tag, account.rfids.all())
        self.assertEqual(
            Transaction.objects.filter(account=account, rfid=tag.rfid).count(), 2
        )
        self.assertLess(account.balance_kw, 0)

