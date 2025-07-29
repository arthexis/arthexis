from django.test import Client, TestCase
from django.urls import reverse

from .models import User, BlacklistedRFID, Account, Vehicle
from django.core.exceptions import ValidationError


class RFIDLoginTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="alice", password="secret", rfid_uid="CARD123"
        )

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
            username="eve", password="secret", rfid_uid="BAD123"
        )

    def test_blacklist_removes_and_blocks(self):
        BlacklistedRFID.objects.create(uid="BAD123")
        self.user.refresh_from_db()
        self.assertIsNone(self.user.rfid_uid)

        with self.assertRaises(ValidationError):
            User.objects.create_user(
                username="bob", password="pwd", rfid_uid="BAD123"
            )


class AccountTests(TestCase):
    def test_balance_calculation(self):
        user = User.objects.create_user(username="balance", password="x")
        acc = Account.objects.create(user=user, credits_kwh=50, total_kwh_spent=20)
        self.assertEqual(acc.balance_kwh, 30)


class VehicleTests(TestCase):
    def test_account_can_have_multiple_vehicles(self):
        user = User.objects.create_user(username="cars", password="x")
        acc = Account.objects.create(user=user)
        Vehicle.objects.create(account=acc, brand="Tesla", model="Model S", vin="VIN12345678901234")
        Vehicle.objects.create(account=acc, brand="Nissan", model="Leaf", vin="VIN23456789012345")
        self.assertEqual(acc.vehicles.count(), 2)
