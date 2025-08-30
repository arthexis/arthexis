import os
import sys
import json
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from core.models import Reference


class ReferenceTransactionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="transadm",
            email="trans@example.com",
            password="password",
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_transaction_uuid_auto_and_existing(self):
        ref1 = Reference.objects.create(alt_text="r1", value="v1")
        self.assertIsNotNone(ref1.transaction_uuid)
        ref2 = Reference.objects.create(
            alt_text="r2",
            value="v2",
            transaction_uuid=ref1.transaction_uuid,
        )
        self.assertEqual(ref1.transaction_uuid, ref2.transaction_uuid)
        with self.assertRaises(Exception):
            ref1.transaction_uuid = uuid.uuid4()
            ref1.save()

    def test_bulk_create_assigns_same_transaction(self):
        url = reverse("admin:core_reference_bulk")
        data = {
            "references": [
                {"alt_text": "A", "value": "1"},
                {"alt_text": "B", "value": "2"},
            ]
        }
        resp = self.client.post(
            url, data=json.dumps(data), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        tx = resp.json()["transaction_uuid"]
        refs = Reference.objects.filter(transaction_uuid=tx)
        self.assertEqual(refs.count(), 2)
