from django.test import Client, TestCase
from django.urls import reverse

from .models import User, RFID, Account
from django.core.exceptions import ValidationError
from django.db import IntegrityError


class RFIDLoginTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="alice", password="secret"
        )
        RFID.objects.create(uid="CARD123", user=self.user)

    def test_rfid_login_success(self):
        response = self.client.post(
            reverse("rfid-login"),
            data={"rfid": "CARD123"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "alice")

    def test_rfid_login_invalid(self):
        response = self.client.post(
            reverse("rfid-login"),
            data={"rfid": "UNKNOWN"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class BlacklistRFIDTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="eve", password="secret"
        )
        self.rfid = RFID.objects.create(uid="BAD123", user=self.user)

    def test_blacklist_removes_and_blocks(self):
        self.rfid.blacklisted = True
        self.rfid.save()
        self.user.refresh_from_db()
        self.assertFalse(self.user.rfids.exists())

        with self.assertRaises(IntegrityError):
            RFID.objects.create(uid="BAD123", user=self.user)


class AccountTests(TestCase):
    def test_balance_calculation(self):
        user = User.objects.create_user(username="balance", password="x")
        acc = Account.objects.create(user=user, credits_kwh=50, total_kwh_spent=20)
        self.assertEqual(acc.balance_kwh, 30)
