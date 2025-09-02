import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from django.test import Client, TestCase
from django.urls import reverse
from django.http import HttpRequest
import json
from unittest import mock

from django.utils import timezone
from .models import (
    User,
    EnergyAccount,
    ElectricVehicle,
    EnergyCredit,
    Address,
    Product,
    Subscription,
    Brand,
    EVModel,
    RFID,
    FediverseProfile,
    SecurityGroup,
)
from ocpp.models import Transaction, Charger

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from .backends import LocalhostAdminBackend


class DefaultAdminTests(TestCase):
    def test_arthexis_is_default_user(self):
        self.assertTrue(User.objects.filter(username="arthexis").exists())
        self.assertFalse(User.all_objects.filter(username="admin").exists())

    def test_admin_created_and_local_only(self):
        backend = LocalhostAdminBackend()
        req = HttpRequest()
        req.META["REMOTE_ADDR"] = "127.0.0.1"
        user = backend.authenticate(req, username="admin", password="admin")
        self.assertIsNotNone(user)
        self.assertEqual(user.pk, 2)

        remote = HttpRequest()
        remote.META["REMOTE_ADDR"] = "10.0.0.1"
        self.assertIsNone(
            backend.authenticate(remote, username="admin", password="admin")
        )

    def test_admin_respects_forwarded_for(self):
        backend = LocalhostAdminBackend()

        req = HttpRequest()
        req.META["REMOTE_ADDR"] = "10.0.0.1"
        req.META["HTTP_X_FORWARDED_FOR"] = "127.0.0.1"
        self.assertIsNotNone(
            backend.authenticate(req, username="admin", password="admin"),
            "X-Forwarded-For should permit allowed IP",
        )

        blocked = HttpRequest()
        blocked.META["REMOTE_ADDR"] = "10.0.0.1"
        blocked.META["HTTP_X_FORWARDED_FOR"] = "8.8.8.8"
        self.assertIsNone(
            backend.authenticate(blocked, username="admin", password="admin")
        )


class RFIDLoginTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="alice", password="secret")
        self.account = EnergyAccount.objects.create(user=self.user, name="ALICE")
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


class RFIDBatchApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="bob", password="secret")
        self.account = EnergyAccount.objects.create(user=self.user, name="BOB")
        self.client.force_login(self.user)

    def test_export_rfids(self):
        tag_black = RFID.objects.create(rfid="CARD999")
        tag_white = RFID.objects.create(rfid="CARD998", color=RFID.WHITE)
        self.account.rfids.add(tag_black, tag_white)
        response = self.client.get(reverse("rfid-batch"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "rfids": [
                    {
                        "rfid": "CARD999",
                        "energy_accounts": [self.account.id],
                        "allowed": True,
                        "color": "B",
                        "released": False,
                    }
                ]
            },
        )

    def test_export_rfids_color_filter(self):
        RFID.objects.create(rfid="CARD111", color=RFID.WHITE)
        response = self.client.get(reverse("rfid-batch"), {"color": "W"})
        self.assertEqual(
            response.json(),
            {
                "rfids": [
                    {
                        "rfid": "CARD111",
                        "energy_accounts": [],
                        "allowed": True,
                        "color": "W",
                        "released": False,
                    }
                ]
            },
        )

    def test_export_rfids_released_filter(self):
        RFID.objects.create(rfid="CARD112", released=True)
        RFID.objects.create(rfid="CARD113", released=False)
        response = self.client.get(reverse("rfid-batch"), {"released": "true"})
        self.assertEqual(
            response.json(),
            {
                "rfids": [
                    {
                        "rfid": "CARD112",
                        "energy_accounts": [],
                        "allowed": True,
                        "color": "B",
                        "released": True,
                    }
                ]
            },
        )

    def test_import_rfids(self):
        data = {
            "rfids": [
                {
                    "rfid": "A1B2C3D4",
                    "energy_accounts": [self.account.id],
                    "allowed": True,
                    "color": "W",
                    "released": True,
                }
            ]
        }
        response = self.client.post(
            reverse("rfid-batch"),
            data=json.dumps(data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported"], 1)
        self.assertTrue(
            RFID.objects.filter(
                rfid="A1B2C3D4",
                energy_accounts=self.account,
                color=RFID.WHITE,
                released=True,
            ).exists()
        )


class AllowedRFIDTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="eve", password="secret")
        self.account = EnergyAccount.objects.create(user=self.user, name="EVE")
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

    def test_long_rfid_allowed(self):
        tag = RFID.objects.create(rfid="DEADBEEF10")
        self.assertEqual(tag.rfid, "DEADBEEF10")

    def test_find_user_by_rfid(self):
        user = User.objects.create_user(username="finder", password="pwd")
        acc = EnergyAccount.objects.create(user=user, name="FINDER")
        tag = RFID.objects.create(rfid="ABCD1234")
        acc.rfids.add(tag)
        found = RFID.get_account_by_rfid("abcd1234")
        self.assertEqual(found, acc)


class RFIDAssignmentTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="user1", password="x")
        self.user2 = User.objects.create_user(username="user2", password="x")
        self.acc1 = EnergyAccount.objects.create(user=self.user1, name="USER1")
        self.acc2 = EnergyAccount.objects.create(user=self.user2, name="USER2")
        self.tag = RFID.objects.create(rfid="ABCDEF12")

    def test_rfid_can_only_attach_to_one_account(self):
        self.acc1.rfids.add(self.tag)
        with self.assertRaises(ValidationError):
            self.acc2.rfids.add(self.tag)


class EnergyAccountTests(TestCase):
    def test_balance_calculation(self):
        user = User.objects.create_user(username="balance", password="x")
        acc = EnergyAccount.objects.create(user=user, name="BALANCE")
        EnergyCredit.objects.create(account=acc, amount_kw=50)
        charger = Charger.objects.create(charger_id="T1")
        Transaction.objects.create(
            charger=charger,
            account=acc,
            meter_start=0,
            meter_stop=20,
            start_time=timezone.now(),
            stop_time=timezone.now(),
        )
        self.assertEqual(acc.total_kw_spent, 20)
        self.assertEqual(acc.balance_kw, 30)

    def test_authorization_requires_positive_balance(self):
        user = User.objects.create_user(username="auth", password="x")
        acc = EnergyAccount.objects.create(user=user, name="AUTH")
        self.assertFalse(acc.can_authorize())

        EnergyCredit.objects.create(account=acc, amount_kw=5)
        self.assertTrue(acc.can_authorize())

    def test_service_account_ignores_balance(self):
        user = User.objects.create_user(username="service", password="x")
        acc = EnergyAccount.objects.create(user=user, service_account=True, name="SERVICE")
        self.assertTrue(acc.can_authorize())

    def test_account_without_user(self):
        acc = EnergyAccount.objects.create(name="NOUSER")
        tag = RFID.objects.create(rfid="NOUSER1")
        acc.rfids.add(tag)
        self.assertIsNone(acc.user)
        self.assertTrue(acc.rfids.filter(rfid="NOUSER1").exists())


class ElectricVehicleTests(TestCase):
    def test_account_can_have_multiple_vehicles(self):
        user = User.objects.create_user(username="cars", password="x")
        acc = EnergyAccount.objects.create(user=user, name="CARS")
        tesla = Brand.objects.create(name="Tesla")
        nissan = Brand.objects.create(name="Nissan")
        model_s = EVModel.objects.create(brand=tesla, name="Model S")
        leaf = EVModel.objects.create(brand=nissan, name="Leaf")
        ElectricVehicle.objects.create(
            account=acc, brand=tesla, model=model_s, vin="VIN12345678901234"
        )
        ElectricVehicle.objects.create(
            account=acc, brand=nissan, model=leaf, vin="VIN23456789012345"
        )
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
        self.account = EnergyAccount.objects.create(user=self.user, name="SUBSCRIBER")
        self.product = Product.objects.create(name="Gold", renewal_period=30)
        self.client.force_login(self.user)

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
        details_url = reverse("admin:core_energyaccount_onboard_details")
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
        self.assertEqual(resp.url, reverse("admin:core_energyaccount_changelist"))
        user = User.objects.get(first_name="John", last_name="Doe")
        self.assertFalse(user.is_active)
        account = EnergyAccount.objects.get(user=user)
        self.assertTrue(account.rfids.filter(rfid="ABCD1234").exists())
        self.assertTrue(account.vehicles.filter(vin="VIN12345678901234").exists())


class EVBrandFixtureTests(TestCase):
    def test_ev_brand_fixture_loads(self):
        call_command(
            "loaddata",
            "core/fixtures/ev_brands.json",
            "core/fixtures/ev_models.json",
            verbosity=0,
        )
        porsche = Brand.objects.get(name="Porsche")
        audi = Brand.objects.get(name="Audi")
        self.assertTrue(
            {"WP0", "WP1"} <= set(porsche.wmi_codes.values_list("code", flat=True))
        )
        self.assertTrue(
            set(audi.wmi_codes.values_list("code", flat=True)) >= {"WAU", "TRU"}
        )
        self.assertTrue(EVModel.objects.filter(brand=porsche, name="Taycan").exists())
        self.assertTrue(EVModel.objects.filter(brand=audi, name="e-tron GT").exists())

    def test_brand_from_vin(self):
        call_command(
            "loaddata",
            "core/fixtures/ev_brands.json",
            verbosity=0,
        )
        self.assertEqual(Brand.from_vin("WP0ZZZ12345678901").name, "Porsche")
        self.assertEqual(Brand.from_vin("WAUZZZ12345678901").name, "Audi")
        self.assertIsNone(Brand.from_vin("XYZ12345678901234"))


class RFIDFixtureTests(TestCase):
    def test_fixture_assigns_gelectriic_rfid(self):
        call_command(
            "loaddata",
            "core/fixtures/users.json",
            "core/fixtures/energy_accounts.json",
            "core/fixtures/rfids.json",
            verbosity=0,
        )
        account = EnergyAccount.objects.get(name="GELECTRIIC")
        tag = RFID.objects.get(rfid="FFFFFFFF")
        self.assertIn(account, tag.energy_accounts.all())
        self.assertEqual(tag.energy_accounts.count(), 1)


class RFIDKeyVerificationFlagTests(TestCase):
    def test_flags_reset_on_key_change(self):
        tag = RFID.objects.create(
            rfid="ABC12345", key_a_verified=True, key_b_verified=True
        )
        tag.key_a = "A1A1A1A1A1A1"
        tag.save()
        self.assertFalse(tag.key_a_verified)
        tag.key_b = "B1B1B1B1B1B1"
        tag.save()
        self.assertFalse(tag.key_b_verified)


class SecurityGroupTests(TestCase):
    def test_parent_and_user_assignment(self):
        parent = SecurityGroup.objects.create(name="Parents")
        child = SecurityGroup.objects.create(name="Children", parent=parent)
        user = User.objects.create_user(username="sg_user", password="secret")
        child.user_set.add(user)
        self.assertEqual(child.parent, parent)
        self.assertIn(user, child.user_set.all())


class FediverseProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fed", password="secret")

    @mock.patch("requests.get")
    def test_connection_success_sets_verified(self, mock_get):
        mock_get.return_value.ok = True
        mock_get.return_value.raise_for_status.return_value = None
        profile = FediverseProfile.objects.create(
            user=self.user,
            service=FediverseProfile.MASTODON,
            host="example.com",
            handle="fed",
            access_token="tok",
        )
        self.assertTrue(profile.test_connection())
        self.assertIsNotNone(profile.verified_on)

    @mock.patch("requests.get", side_effect=Exception("boom"))
    def test_connection_failure_raises(self, mock_get):
        profile = FediverseProfile.objects.create(
            user=self.user,
            service=FediverseProfile.MASTODON,
            host="example.com",
            handle="fed",
        )
        with self.assertRaises(ValidationError):
            profile.test_connection()
        self.assertIsNone(profile.verified_on)

