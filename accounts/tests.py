from django.test import Client, TestCase
from django.urls import reverse
from django.http import HttpRequest

from django.utils import timezone
from .models import User, RFID, Account, Vehicle, Credit, Address, Product, Subscription
from ocpp.models import Transaction

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from .backends import LocalhostAdminBackend


class DefaultAdminTests(TestCase):
    def test_admin_created_and_localhost_only(self):
        self.assertTrue(User.objects.filter(username="admin").exists())
        backend = LocalhostAdminBackend()

        local = HttpRequest()
        local.META["REMOTE_ADDR"] = "127.0.0.1"
        self.assertIsNotNone(
            backend.authenticate(local, username="admin", password="admin")
        )

        remote = HttpRequest()
        remote.META["REMOTE_ADDR"] = "10.0.0.1"
        self.assertIsNone(
            backend.authenticate(remote, username="admin", password="admin")
        )


class RFIDLoginTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="alice", password="secret"
        )
        self.account = Account.objects.create(user=self.user)
        tag = RFID.objects.create(rfid="CARD123")
        self.account.rfids.add(tag)

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


class AllowedRFIDTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="eve", password="secret"
        )
        self.account = Account.objects.create(user=self.user)
        self.rfid = RFID.objects.create(rfid="BAD123")
        self.account.rfids.add(self.rfid)

    def test_disallow_removes_and_blocks(self):
        self.rfid.allowed = False
        self.rfid.save()
        self.account.refresh_from_db()
        self.assertFalse(self.account.rfids.exists())

        with self.assertRaises(IntegrityError):
            RFID.objects.create(rfid="BAD123")


class RFIDValidationTests(TestCase):
    def test_invalid_format_raises(self):
        tag = RFID(rfid="xyz")
        with self.assertRaises(ValidationError):
            tag.full_clean()

    def test_lowercase_saved_uppercase(self):
        tag = RFID.objects.create(rfid="deadbeef")
        self.assertEqual(tag.rfid, "DEADBEEF")

    def test_find_user_by_rfid(self):
        user = User.objects.create_user(username="finder", password="pwd")
        acc = Account.objects.create(user=user)
        tag = RFID.objects.create(rfid="ABCD1234")
        acc.rfids.add(tag)
        found = RFID.get_account_by_rfid("abcd1234")
        self.assertEqual(found, acc)


class AccountTests(TestCase):
    def test_balance_calculation(self):
        user = User.objects.create_user(username="balance", password="x")
        acc = Account.objects.create(user=user)
        Credit.objects.create(account=acc, amount_kwh=50)
        Transaction.objects.create(
            charger_id="T1",
            transaction_id=1,
            account=acc,
            meter_start=0,
            meter_stop=20,
            start_time=timezone.now(),
            stop_time=timezone.now(),
        )
        self.assertEqual(acc.total_kwh_spent, 20)
        self.assertEqual(acc.balance_kwh, 30)

    def test_authorization_requires_positive_balance(self):
        user = User.objects.create_user(username="auth", password="x")
        acc = Account.objects.create(user=user)
        self.assertFalse(acc.can_authorize())

        Credit.objects.create(account=acc, amount_kwh=5)
        self.assertTrue(acc.can_authorize())

    def test_service_account_ignores_balance(self):
        user = User.objects.create_user(username="service", password="x")
        acc = Account.objects.create(user=user, service_account=True)
        self.assertTrue(acc.can_authorize())

    def test_account_without_user(self):
        acc = Account.objects.create()
        tag = RFID.objects.create(rfid="NOUSER1")
        acc.rfids.add(tag)
        self.assertIsNone(acc.user)
        self.assertTrue(acc.rfids.filter(rfid="NOUSER1").exists())


class VehicleTests(TestCase):
    def test_account_can_have_multiple_vehicles(self):
        user = User.objects.create_user(username="cars", password="x")
        acc = Account.objects.create(user=user)
        Vehicle.objects.create(account=acc, brand="Tesla", model="Model S", vin="VIN12345678901234")
        Vehicle.objects.create(account=acc, brand="Nissan", model="Leaf", vin="VIN23456789012345")
        self.assertEqual(acc.vehicles.count(), 2)


class AddressTests(TestCase):
    def test_invalid_municipality_state(self):
        addr = Address(
            street="Main",
            number="1",
            municipality="Monterrey",
            state=Address.State.COAHUILA,
            postal_code="00000",
        )
        with self.assertRaises(ValidationError):
            addr.full_clean()

    def test_user_link(self):
        addr = Address.objects.create(
            street="Main",
            number="2",
            municipality="Monterrey",
            state=Address.State.NUEVO_LEON,
            postal_code="64000",
        )
        user = User.objects.create_user(username="addr", password="pwd", address=addr)
        self.assertEqual(user.address, addr)


class SubscriptionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="bob", password="pwd")
        self.account = Account.objects.create(user=self.user)
        self.product = Product.objects.create(name="Gold", renewal_period=30)

    def test_create_and_list_subscription(self):
        response = self.client.post(
            reverse("add-subscription"),
            data={"account_id": self.account.id, "product_id": self.product.id},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Subscription.objects.count(), 1)

        list_resp = self.client.get(
            reverse("subscription-list"), {"account_id": self.account.id}
        )
        self.assertEqual(list_resp.status_code, 200)
        data = list_resp.json()
        self.assertEqual(len(data["subscriptions"]), 1)
        self.assertEqual(data["subscriptions"][0]["product__name"], "Gold")

    def test_product_list(self):
        response = self.client.get(reverse("product-list"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["products"]), 1)
        self.assertEqual(data["products"][0]["name"], "Gold")


class OnboardingWizardTests(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_superuser("super", "super@example.com", "pwd")
        self.client.force_login(User.objects.get(username="super"))

    def test_onboarding_flow_creates_account(self):
        details_url = reverse("admin:accounts_account_onboard_details")
        response = self.client.get(details_url)
        self.assertEqual(response.status_code, 200)
        data = {
            "first_name": "John",
            "last_name": "Doe",
            "rfid": "ABCD1234",
            "vehicle_id": "VIN12345678901234",
        }
        resp = self.client.post(details_url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("admin:accounts_account_changelist"))
        user = User.objects.get(first_name="John", last_name="Doe")
        self.assertFalse(user.is_active)
        account = Account.objects.get(user=user)
        self.assertTrue(account.rfids.filter(rfid="ABCD1234").exists())
        self.assertTrue(account.vehicles.filter(vin="VIN12345678901234").exists())
