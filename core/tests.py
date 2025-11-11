import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django import forms
from django.test import (
    Client,
    TestCase,
    TransactionTestCase,
    RequestFactory,
    override_settings,
)
from django.urls import reverse
from django.http import HttpRequest
from django.contrib import messages
import csv
import json
import importlib.util
from decimal import Decimal
from unittest import mock
from unittest.mock import patch
from pathlib import Path
import subprocess
import types
from glob import glob
from datetime import datetime, timedelta, timezone as datetime_timezone, time
import tempfile
from io import StringIO
from urllib.parse import quote

from django.utils import timezone
from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from tablib import Dataset
from .models import (
    User,
    UserPhoneNumber,
    CustomerAccount,
    EnergyCredit,
    EnergyTariff,
    Product,
    RFID,
    SecurityGroup,
    Package,
    PackageRelease,
    ReleaseManager,
    Todo,
    PublicWifiAccess,
)
from django.contrib.admin.sites import AdminSite
from core.admin import (
    PackageReleaseAdmin,
    PackageAdmin,
    RFIDResource,
    SecurityGroupAdmin,
    UserAdmin,
    USER_PROFILE_INLINES,
)
from ocpp.models import Brand, ElectricVehicle, EVModel, Transaction, Charger
from nodes.models import ContentSample

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from .backends import LocalhostAdminBackend
from core.views import (
    _step_check_version,
    _step_pre_release_actions,
    _step_promote_build,
    _step_publish,
)
from core import views as core_views
from core import public_wifi


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


class UserOperateAsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.permission = Permission.objects.get(codename="view_todo")

    def test_staff_user_delegates_permissions(self):
        delegate = User.objects.create_user(username="delegate", password="secret")
        delegate.user_permissions.add(self.permission)
        operator = User.objects.create_user(
            username="operator", password="secret", is_staff=True
        )
        self.assertFalse(operator.has_perm("core.view_todo"))
        operator.operate_as = delegate
        operator.full_clean()
        operator.save()
        operator.refresh_from_db()
        self.assertTrue(operator.has_perm("core.view_todo"))

    def test_only_staff_may_operate_as(self):
        delegate = User.objects.create_user(username="delegate", password="secret")
        operator = User.objects.create_user(username="operator", password="secret")
        operator.operate_as = delegate
        with self.assertRaises(ValidationError):
            operator.full_clean()

    def test_non_superuser_cannot_operate_as_staff(self):
        staff_delegate = User.objects.create_user(
            username="delegate", password="secret", is_staff=True
        )
        operator = User.objects.create_user(
            username="operator", password="secret", is_staff=True
        )
        operator.operate_as = staff_delegate
        with self.assertRaises(ValidationError):
            operator.full_clean()

    def test_recursive_chain_and_cycle_detection(self):
        base = User.objects.create_user(username="base", password="secret")
        base.user_permissions.add(self.permission)
        middle = User.objects.create_user(
            username="middle", password="secret", is_staff=True
        )
        middle.operate_as = base
        middle.full_clean()
        middle.save()
        top = User.objects.create_superuser(
            username="top", email="top@example.com", password="secret"
        )
        top.operate_as = middle
        top.full_clean()
        top.save()
        top.refresh_from_db()
        self.assertTrue(top.has_perm("core.view_todo"))

        first = User.objects.create_superuser(
            username="first", email="first@example.com", password="secret"
        )
        second = User.objects.create_superuser(
            username="second", email="second@example.com", password="secret"
        )
        first.operate_as = second
        first.full_clean()
        first.save()
        second.operate_as = first
        second.full_clean()
        second.save()
        self.assertFalse(first._check_operate_as_chain(lambda user: False))

    def test_module_permissions_fall_back(self):
        delegate = User.objects.create_user(username="helper", password="secret")
        delegate.user_permissions.add(self.permission)
        operator = User.objects.create_user(
            username="mod", password="secret", is_staff=True
        )
        operator.operate_as = delegate
        operator.full_clean()
        operator.save()
        self.assertTrue(operator.has_module_perms("core"))

    def test_has_profile_via_delegate(self):
        delegate = User.objects.create_user(
            username="delegate", password="secret", is_staff=True
        )
        ReleaseManager.objects.create(user=delegate)
        operator = User.objects.create_superuser(
            username="operator",
            email="operator@example.com",
            password="secret",
        )
        operator.operate_as = delegate
        operator.full_clean()
        operator.save()
        profile = operator.get_profile(ReleaseManager)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.user, delegate)
        self.assertTrue(operator.has_profile(ReleaseManager))

    def test_has_profile_via_group_membership(self):
        member = User.objects.create_user(username="member", password="secret")
        group = SecurityGroup.objects.create(name="Managers")
        group.user_set.add(member)
        profile = ReleaseManager.objects.create(group=group)
        self.assertEqual(member.get_profile(ReleaseManager), profile)
        self.assertTrue(member.has_profile(ReleaseManager))

    def test_release_manager_property_uses_delegate_profile(self):
        delegate = User.objects.create_user(
            username="delegate-property", password="secret", is_staff=True
        )
        profile = ReleaseManager.objects.create(user=delegate)
        operator = User.objects.create_superuser(
            username="operator-property",
            email="operator-property@example.com",
            password="secret",
        )
        operator.operate_as = delegate
        operator.full_clean()
        operator.save()
        self.assertEqual(operator.release_manager, profile)


class UserPhoneNumberTests(TestCase):
    def test_get_phone_numbers_by_priority(self):
        user = User.objects.create_user(username="phone-user", password="secret")
        later = UserPhoneNumber.objects.create(
            user=user, number="+15555550101", priority=10
        )
        earlier = UserPhoneNumber.objects.create(
            user=user, number="+15555550100", priority=1
        )
        immediate = UserPhoneNumber.objects.create(
            user=user, number="+15555550099", priority=0
        )

        phones = user.get_phones_by_priority()
        self.assertEqual(phones, [immediate, earlier, later])

    def test_get_phone_numbers_by_priority_orders_by_id_when_equal(self):
        user = User.objects.create_user(username="phone-order", password="secret")
        first = UserPhoneNumber.objects.create(
            user=user, number="+19995550000", priority=0
        )
        second = UserPhoneNumber.objects.create(
            user=user, number="+19995550001", priority=0
        )

        phones = user.get_phones_by_priority()
        self.assertEqual(phones, [first, second])

    def test_get_phone_numbers_by_priority_alias(self):
        user = User.objects.create_user(username="phone-alias", password="secret")
        phone = UserPhoneNumber.objects.create(
            user=user, number="+14445550000", priority=3
        )

        self.assertEqual(user.get_phone_numbers_by_priority(), [phone])


class ProfileValidationTests(TestCase):
    def test_system_user_cannot_receive_profiles(self):
        system_user = User.objects.get(username=User.SYSTEM_USERNAME)
        profile = ReleaseManager(user=system_user)
        with self.assertRaises(ValidationError) as exc:
            profile.full_clean()
        self.assertIn("user", exc.exception.error_dict)


class UserAdminInlineTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.factory = RequestFactory()
        self.admin = UserAdmin(User, self.site)
        self.system_user = User.objects.get(username=User.SYSTEM_USERNAME)
        self.superuser = User.objects.create_superuser(
            username="inline-super",
            email="inline-super@example.com",
            password="secret",
        )

    def test_profile_inlines_hidden_for_system_user(self):
        request = self.factory.get("/")
        request.user = self.superuser
        system_inlines = self.admin.get_inline_instances(request, self.system_user)
        system_profiles = [
            inline
            for inline in system_inlines
            if inline.__class__ in USER_PROFILE_INLINES
        ]
        self.assertFalse(system_profiles)

        other_inlines = self.admin.get_inline_instances(request, self.superuser)
        other_profiles = [
            inline
            for inline in other_inlines
            if inline.__class__ in USER_PROFILE_INLINES
        ]
        self.assertEqual(len(other_profiles), len(USER_PROFILE_INLINES))


class SecurityGroupAdminTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = SecurityGroupAdmin(SecurityGroup, self.site)

    def test_search_fields_include_name_and_parent(self):
        self.assertIn("name", self.admin.search_fields)
        self.assertIn("parent__name", self.admin.search_fields)


class RFIDLoginTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="alice", password="secret")
        self.account = CustomerAccount.objects.create(user=self.user, name="ALICE")
        tag = RFID.objects.create(rfid="CARD123")
        self.account.rfids.add(tag)

    def test_rfid_login_success(self):
        response = self.client.post(
            reverse("rfid-login"),
            data={"rfid": "CARD123"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["username"], "alice")
        self.assertEqual(payload["redirect"], "/")

    def test_rfid_login_invalid(self):
        response = self.client.post(
            reverse("rfid-login"),
            data={"rfid": "UNKNOWN"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    @patch("core.backends.subprocess.run")
    def test_rfid_login_external_command_success(self, mock_run):
        tag = self.account.rfids.first()
        tag.external_command = "echo ok"
        tag.save(update_fields=["external_command"])
        mock_run.return_value = types.SimpleNamespace(returncode=0)

        response = self.client.post(
            reverse("rfid-login"),
            data={"rfid": "CARD123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("redirect"), "/")
        run_args, run_kwargs = mock_run.call_args
        self.assertEqual(run_args[0], "echo ok")
        self.assertTrue(run_kwargs.get("shell"))
        env = run_kwargs.get("env", {})
        self.assertEqual(env.get("RFID_VALUE"), "CARD123")
        self.assertEqual(env.get("RFID_LABEL_ID"), str(tag.pk))

    @patch("core.backends.subprocess.run")
    def test_rfid_login_external_command_failure(self, mock_run):
        tag = self.account.rfids.first()
        tag.external_command = "exit 1"
        tag.save(update_fields=["external_command"])
        mock_run.return_value = types.SimpleNamespace(returncode=1)

        response = self.client.post(
            reverse("rfid-login"),
            data={"rfid": "CARD123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        mock_run.assert_called_once()

    @patch("core.backends.subprocess.Popen")
    def test_rfid_login_post_command_runs_after_success(self, mock_popen):
        tag = self.account.rfids.first()
        tag.post_auth_command = "echo welcome"
        tag.save(update_fields=["post_auth_command"])

        response = self.client.post(
            reverse("rfid-login"),
            data={"rfid": "CARD123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("redirect"), "/")
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], "echo welcome")
        self.assertTrue(kwargs.get("shell"))
        env = kwargs.get("env", {})
        self.assertEqual(env.get("RFID_VALUE"), "CARD123")
        self.assertEqual(env.get("RFID_LABEL_ID"), str(tag.pk))
        self.assertIs(kwargs.get("stdout"), subprocess.DEVNULL)
        self.assertIs(kwargs.get("stderr"), subprocess.DEVNULL)

    @patch("core.backends.subprocess.Popen")
    def test_rfid_login_post_command_skipped_on_failure(self, mock_popen):
        tag = self.account.rfids.first()
        tag.post_auth_command = "echo welcome"
        tag.allowed = False
        tag.save(update_fields=["post_auth_command", "allowed"])

        response = self.client.post(
            reverse("rfid-login"),
            data={"rfid": "CARD123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        mock_popen.assert_not_called()

    def test_rfid_login_uses_next_redirect(self):
        response = self.client.post(
            reverse("rfid-login"),
            data={"rfid": "CARD123", "next": "/dashboard/"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("redirect"), "/dashboard/")

    def test_rfid_login_ignores_unsafe_redirect(self):
        response = self.client.post(
            reverse("rfid-login"),
            data={"rfid": "CARD123", "next": "https://example.com/"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("redirect"), "/")


class RFIDBatchApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="bob", password="secret")
        self.account = CustomerAccount.objects.create(user=self.user, name="BOB")
        self.client.force_login(self.user)

    def test_export_rfids(self):
        tag_black = RFID.objects.create(rfid="CARD999", custom_label="Main Tag")
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
                        "custom_label": "Main Tag",
                        "energy_accounts": [self.account.id],
                        "external_command": "",
                        "post_auth_command": "",
                        "allowed": True,
                        "color": "B",
                        "released": False,
                    }
                ]
            },
        )


class UserAdminPasswordChangeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="adminpass"
        )
        self.user = User.objects.create_user(username="target", password="oldpass")
        self.client.force_login(self.admin)

    def test_change_password_form_excludes_rfid_field(self):
        url = reverse("admin:core_user_password_change", args=[self.user.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="rfid"')
        self.assertNotContains(response, "Login RFID")

    def test_change_password_does_not_alter_existing_rfid_assignment(self):
        account = CustomerAccount.objects.create(user=self.user, name="TARGET")
        tag = RFID.objects.create(rfid="CARD778")
        account.rfids.add(tag)
        url = reverse("admin:core_user_password_change", args=[self.user.pk])
        response = self.client.post(
            url,
            {
                "new_password1": "NewStrongPass123",
                "new_password2": "NewStrongPass123",
            },
        )
        self.assertRedirects(
            response, reverse("admin:core_user_change", args=[self.user.pk])
        )
        account.refresh_from_db()
        self.assertTrue(account.rfids.filter(pk=tag.pk).exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewStrongPass123"))


class UserAdminLoginRFIDTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="adminpass"
        )
        self.user = User.objects.create_user(username="target", password="oldpass")
        self.client.force_login(self.admin)

    def _build_change_form_data(self, response, **overrides):
        form = response.context["adminform"].form
        data = {}
        for name, field in form.fields.items():
            if isinstance(field, forms.ModelMultipleChoiceField):
                initial = form.initial.get(name, field.initial) or []
                data[name] = [str(value.pk) for value in initial]
            elif isinstance(field, forms.BooleanField):
                initial = form.initial.get(name, field.initial)
                if initial:
                    data[name] = "on"
            elif name != "login_rfid":
                value = form.initial.get(name, field.initial)
                data[name] = "" if value is None else value
        data["_save"] = "Save"
        data.update({key: value for key, value in overrides.items() if value is not None})
        return data

    def test_change_form_includes_login_rfid_field(self):
        tag = RFID.objects.create(rfid="CARD777")
        CustomerAccount.objects.create(user=self.user, name="TARGET")
        url = reverse("admin:core_user_change", args=[self.user.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="login_rfid"')
        self.assertContains(response, str(tag.pk))

    def test_change_form_assigns_login_rfid(self):
        account = CustomerAccount.objects.create(user=self.user, name="TARGET")
        tag = RFID.objects.create(rfid="CARD778")
        url = reverse("admin:core_user_change", args=[self.user.pk])
        response = self.client.get(url)
        form_data = self._build_change_form_data(response, login_rfid=str(tag.pk))
        post_response = self.client.post(url, form_data)
        self.assertRedirects(
            post_response, reverse("admin:core_user_change", args=[self.user.pk])
        )
        account.refresh_from_db()
        self.assertTrue(account.rfids.filter(pk=tag.pk).exists())

    def test_change_form_creates_customer_account_when_missing(self):
        tag = RFID.objects.create(rfid="CARD779")
        url = reverse("admin:core_user_change", args=[self.user.pk])
        response = self.client.get(url)
        form_data = self._build_change_form_data(response, login_rfid=str(tag.pk))
        post_response = self.client.post(url, form_data)
        self.assertRedirects(
            post_response, reverse("admin:core_user_change", args=[self.user.pk])
        )
        account = CustomerAccount.objects.get(user=self.user)
        self.assertTrue(account.rfids.filter(pk=tag.pk).exists())


class AdminSitePasswordChangeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="adminpass"
        )
        self.client.force_login(self.admin)

    def test_admin_password_change_form_excludes_rfid_field(self):
        response = self.client.get(reverse("admin:password_change"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="rfid"')
        self.assertNotContains(response, "Login RFID")

    def test_admin_password_change_keeps_existing_rfid_assignment(self):
        account = CustomerAccount.objects.create(user=self.admin, name="ADMIN")
        tag = RFID.objects.create(rfid="CARD881")
        account.rfids.add(tag)
        response = self.client.post(
            reverse("admin:password_change"),
            {
                "old_password": "adminpass",
                "new_password1": "UltraSecurePass123",
                "new_password2": "UltraSecurePass123",
                "password1": "UltraSecurePass123",
                "password2": "UltraSecurePass123",
            },
        )
        self.assertRedirects(response, reverse("admin:password_change_done"))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("UltraSecurePass123"))
        account.refresh_from_db()
        self.assertTrue(account.rfids.filter(pk=tag.pk).exists())

    def test_export_rfids_color_filter(self):
        RFID.objects.create(rfid="CARD111", color=RFID.WHITE)
        response = self.client.get(reverse("rfid-batch"), {"color": "W"})
        self.assertEqual(
            response.json(),
            {
                "rfids": [
                    {
                        "rfid": "CARD111",
                        "custom_label": "",
                        "energy_accounts": [],
                        "external_command": "",
                        "post_auth_command": "",
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
                        "custom_label": "",
                        "energy_accounts": [],
                        "external_command": "",
                        "post_auth_command": "",
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
                    "custom_label": "Imported Tag",
                    "energy_accounts": [self.account.id],
                    "external_command": "echo pre",
                    "post_auth_command": "echo post",
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
                custom_label="Imported Tag",
                energy_accounts=self.account,
                external_command="echo pre",
                post_auth_command="echo post",
                color=RFID.WHITE,
                released=True,
            ).exists()
        )


class AllowedRFIDTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="eve", password="secret")
        self.account = CustomerAccount.objects.create(user=self.user, name="EVE")
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

    def test_reversed_uid_updates_with_rfid(self):
        tag = RFID.objects.create(rfid="A1B2C3D4")
        self.assertEqual(tag.reversed_uid, "D4C3B2A1")

        tag.rfid = "112233"
        tag.save(update_fields=["rfid"])
        tag.refresh_from_db()
        self.assertEqual(tag.reversed_uid, "332211")

    def test_find_user_by_rfid(self):
        user = User.objects.create_user(username="finder", password="pwd")
        acc = CustomerAccount.objects.create(user=user, name="FINDER")
        tag = RFID.objects.create(rfid="ABCD1234")
        acc.rfids.add(tag)
        found = RFID.get_account_by_rfid("abcd1234")
        self.assertEqual(found, acc)

    def test_custom_label_length(self):
        tag = RFID(rfid="FACE1234", custom_label="x" * 33)
        with self.assertRaises(ValidationError):
            tag.full_clean()


class RFIDLabelSequenceTests(TestCase):
    def test_next_scan_label_starts_at_ten(self):
        self.assertEqual(RFID.next_scan_label(), 10)

    def test_next_scan_label_skips_non_multiples(self):
        RFID.objects.create(label_id=21, rfid="SEQTEST21")

        self.assertEqual(RFID.next_scan_label(), 30)

    def test_next_copy_label_increments_by_one(self):
        source = RFID.objects.create(label_id=40, rfid="SEQTEST40")

        self.assertEqual(RFID.next_copy_label(source), 41)

    def test_next_copy_label_skips_existing(self):
        source = RFID.objects.create(label_id=50, rfid="SEQTEST50")
        RFID.objects.create(label_id=51, rfid="SEQTEST51")

        self.assertEqual(RFID.next_copy_label(source), 52)


class RFIDAssignmentTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="user1", password="x")
        self.user2 = User.objects.create_user(username="user2", password="x")
        self.acc1 = CustomerAccount.objects.create(user=self.user1, name="USER1")
        self.acc2 = CustomerAccount.objects.create(user=self.user2, name="USER2")
        self.tag = RFID.objects.create(rfid="ABCDEF12")

    def test_rfid_can_only_attach_to_one_account(self):
        self.acc1.rfids.add(self.tag)
        with self.assertRaises(ValidationError):
            self.acc2.rfids.add(self.tag)


class CustomerAccountTests(TestCase):
    def test_balance_calculation(self):
        user = User.objects.create_user(username="balance", password="x")
        acc = CustomerAccount.objects.create(user=user, name="BALANCE")
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
        acc = CustomerAccount.objects.create(user=user, name="AUTH")
        self.assertFalse(acc.can_authorize())

        EnergyCredit.objects.create(account=acc, amount_kw=5)
        self.assertTrue(acc.can_authorize())

    def test_service_account_ignores_balance(self):
        user = User.objects.create_user(username="service", password="x")
        acc = CustomerAccount.objects.create(
            user=user, service_account=True, name="SERVICE"
        )
        self.assertTrue(acc.can_authorize())

    def test_potential_purchase_kw_from_balance(self):
        user = User.objects.create_user(username="tariff", password="x")
        tariff = EnergyTariff.objects.create(
            year=2025,
            season=EnergyTariff.Season.ANNUAL,
            zone=EnergyTariff.Zone.ONE,
            contract_type=EnergyTariff.ContractType.DOMESTIC,
            period=EnergyTariff.Period.FLAT,
            unit=EnergyTariff.Unit.KWH,
            start_time=time(hour=0, minute=0),
            end_time=time(hour=23, minute=59),
            price_mxn=Decimal("2.5000"),
            cost_mxn=Decimal("2.0000"),
        )
        acc = CustomerAccount.objects.create(
            user=user,
            name="TARIFF",
            balance_mxn=Decimal("50"),
            energy_tariff=tariff,
        )
        expected_kw = acc.balance_mxn / tariff.price_mxn
        self.assertEqual(acc.potential_purchase_kw, expected_kw)
        self.assertTrue(acc.can_authorize())

    def test_account_without_user(self):
        acc = CustomerAccount.objects.create(name="NOUSER")
        tag = RFID.objects.create(rfid="NOUSER1")
        acc.rfids.add(tag)
        self.assertIsNone(acc.user)
        self.assertTrue(acc.rfids.filter(rfid="NOUSER1").exists())


class ElectricVehicleTests(TestCase):
    def test_account_can_have_multiple_vehicles(self):
        user = User.objects.create_user(username="cars", password="x")
        acc = CustomerAccount.objects.create(user=user, name="CARS")
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


class PublicWifiUtilitiesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="wifi", password="pwd")

    def test_grant_public_access_records_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            allow_file = base / "locks" / "public_wifi_allow.list"
            with override_settings(BASE_DIR=base):
                with patch("core.public_wifi._iptables_available", return_value=False):
                    public_wifi.grant_public_access(self.user, "AA:BB:CC:DD:EE:FF")
            self.assertTrue(allow_file.exists())
            content = allow_file.read_text()
            self.assertIn("aa:bb:cc:dd:ee:ff", content)
            self.assertTrue(
                PublicWifiAccess.objects.filter(
                    user=self.user, mac_address="aa:bb:cc:dd:ee:ff"
                ).exists()
            )

    def test_revoke_public_access_for_user_updates_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            allow_file = base / "locks" / "public_wifi_allow.list"
            with override_settings(BASE_DIR=base):
                with patch("core.public_wifi._iptables_available", return_value=False):
                    access = public_wifi.grant_public_access(
                        self.user, "AA:BB:CC:DD:EE:FF"
                    )
                    public_wifi.revoke_public_access_for_user(self.user)
            access.refresh_from_db()
            self.assertIsNotNone(access.revoked_on)
            if allow_file.exists():
                self.assertNotIn("aa:bb:cc:dd:ee:ff", allow_file.read_text())

    def test_allow_mac_configures_drop_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            executed_rules = []

            def fake_run(args, **kwargs):
                from types import SimpleNamespace

                if args[0] != "iptables":
                    return subprocess.run(args, **kwargs)
                command = args[1:]
                if command[:2] == ["-C", "FORWARD"] and "--mac-source" in command:
                    return SimpleNamespace(returncode=1, stdout="")
                if command[:2] == ["-C", "FORWARD"] and "-o" in command and "DROP" in command:
                    return SimpleNamespace(returncode=1, stdout="")
                if command[0] in {"-A", "-I", "-D"}:
                    executed_rules.append(command)
                    return SimpleNamespace(returncode=0, stdout="")
                return SimpleNamespace(returncode=0, stdout="")

            with override_settings(BASE_DIR=base):
                with patch("core.public_wifi._iptables_available", return_value=True):
                    with patch("core.public_wifi.subprocess.run", side_effect=fake_run):
                        public_wifi.grant_public_access(self.user, "AA:BB:CC:DD:EE:FF")

            self.assertIn(
                ["-A", "FORWARD", "-i", "wlan0", "-o", "wlan1", "-j", "DROP"],
                executed_rules,
            )
            self.assertIn(
                [
                    "-I",
                    "FORWARD",
                    "1",
                    "-i",
                    "wlan0",
                    "-m",
                    "mac",
                    "--mac-source",
                    "aa:bb:cc:dd:ee:ff",
                    "-j",
                    "ACCEPT",
                ],
                executed_rules,
            )


class LiveSubscriptionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="bob", password="pwd")
        self.account = CustomerAccount.objects.create(user=self.user, name="SUBSCRIBER")
        self.product = Product.objects.create(name="Gold", renewal_period=30)
        self.client.force_login(self.user)

    def test_create_and_list_live_subscription(self):
        response = self.client.post(
            reverse("add-live-subscription"),
            data={"account_id": self.account.id, "product_id": self.product.id},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.account.refresh_from_db()
        self.assertEqual(
            self.account.live_subscription_product,
            self.product,
        )
        self.assertIsNotNone(self.account.live_subscription_start_date)
        self.assertEqual(
            self.account.live_subscription_start_date,
            timezone.localdate(),
        )
        self.assertEqual(
            self.account.live_subscription_next_renewal,
            self.account.live_subscription_start_date
            + timedelta(days=self.product.renewal_period),
        )

        list_resp = self.client.get(
            reverse("live-subscription-list"), {"account_id": self.account.id}
        )
        self.assertEqual(list_resp.status_code, 200)
        data = list_resp.json()
        self.assertEqual(len(data["live_subscriptions"]), 1)
        self.assertEqual(data["live_subscriptions"][0]["product__name"], "Gold")
        self.assertEqual(data["live_subscriptions"][0]["id"], self.account.id)
        self.assertEqual(
            data["live_subscriptions"][0]["next_renewal"],
            str(self.account.live_subscription_next_renewal),
        )

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
        details_url = reverse("admin:core_customeraccount_onboard_details")
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
        self.assertEqual(resp.url, reverse("admin:core_customeraccount_changelist"))
        user = User.objects.get(first_name="John", last_name="Doe")
        self.assertFalse(user.is_active)
        account = CustomerAccount.objects.get(user=user)
        self.assertTrue(account.rfids.filter(rfid="ABCD1234").exists())
        self.assertTrue(account.vehicles.filter(vin="VIN12345678901234").exists())


class EVBrandFixtureTests(TestCase):
    def test_ev_brand_fixture_loads(self):
        call_command(
            "loaddata",
            *sorted(glob("ocpp/fixtures/ev_brands__*.json")),
            *sorted(glob("ocpp/fixtures/ev_models__*.json")),
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
        self.assertTrue(EVModel.objects.filter(brand=porsche, name="Macan").exists())
        model3 = EVModel.objects.get(brand__name="Tesla", name="Model 3 RWD")
        self.assertEqual(model3.est_battery_kwh, Decimal("57.50"))

    def test_brand_from_vin(self):
        call_command(
            "loaddata",
            *sorted(glob("ocpp/fixtures/ev_brands__*.json")),
            verbosity=0,
        )
        self.assertEqual(Brand.from_vin("WP0ZZZ12345678901").name, "Porsche")
        self.assertEqual(Brand.from_vin("WAUZZZ12345678901").name, "Audi")
        self.assertIsNone(Brand.from_vin("XYZ12345678901234"))


class RFIDFixtureTests(TestCase):
    def test_fixture_assigns_gelectriic_rfid(self):
        call_command(
            "loaddata",
            "core/fixtures/users__arthexis.json",
            "core/fixtures/energy_accounts__gelectriic.json",
            "core/fixtures/rfids__ffffffff.json",
            verbosity=0,
        )
        account = CustomerAccount.objects.get(name="GELECTRIIC")
        tag = RFID.objects.get(rfid="FFFFFFFF")
        self.assertIn(account, tag.energy_accounts.all())
        self.assertEqual(tag.energy_accounts.count(), 1)


class RFIDImportExportCommandTests(TestCase):
    def test_export_supports_account_names(self):
        account = CustomerAccount.objects.create(name="PRIMARY")
        tag = RFID.objects.create(rfid="CARD500")
        tag.energy_accounts.add(account)

        temp_file = tempfile.NamedTemporaryFile(delete=False)
        temp_file.close()
        rows = []
        try:
            call_command("export_rfids", temp_file.name, account_field="name")
            with open(temp_file.name, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
        finally:
            os.unlink(temp_file.name)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["customer_account_names"], "PRIMARY")

    def test_import_creates_missing_account_by_name(self):
        temp_file = tempfile.NamedTemporaryFile("w", newline="", delete=False)
        try:
            writer = csv.writer(temp_file)
            writer.writerow(
                [
                    "rfid",
                    "custom_label",
                    "customer_account_names",
                    "allowed",
                    "color",
                    "released",
                ]
            )
            writer.writerow(
                [
                    "NAMETAG001",
                    "",
                    "Imported Account",
                    "true",
                    RFID.WHITE,
                    "true",
                ]
            )
            temp_file.flush()
        finally:
            temp_file.close()

        try:
            call_command("import_rfids", temp_file.name, account_field="name")
        finally:
            os.unlink(temp_file.name)

        account = CustomerAccount.objects.get(name="IMPORTED ACCOUNT")
        tag = RFID.objects.get(rfid="NAMETAG001")
        self.assertIsNone(account.user)
        self.assertTrue(tag.energy_accounts.filter(pk=account.pk).exists())

    def test_admin_export_supports_account_names(self):
        account = CustomerAccount.objects.create(name="PRIMARY")
        tag = RFID.objects.create(rfid="CARD501")
        tag.energy_accounts.add(account)

        resource = RFIDResource(account_field="name")
        dataset = resource.export(queryset=RFID.objects.order_by("rfid"))

        self.assertIn("customer_account_names", dataset.headers)
        self.assertEqual(dataset.dict[0]["customer_account_names"], "PRIMARY")

    def test_admin_import_creates_missing_account_by_name(self):
        resource = RFIDResource(account_field="name")
        headers = resource.get_export_headers()
        dataset = Dataset()
        dataset.headers = headers
        row = {header: "" for header in headers}
        row.update(
            {
                "label_id": "200",
                "rfid": "NAMETAG002",
                "custom_label": "",
                "customer_account_names": "Imported Admin Account",
                "allowed": "true",
                "color": RFID.BLACK,
                "kind": RFID.CLASSIC,
                "released": "false",
            }
        )
        dataset.append([row[h] for h in headers])

        result = resource.import_data(dataset, dry_run=False)
        self.assertFalse(result.has_errors())

    def test_admin_import_restores_soft_deleted_label(self):
        tag = RFID.objects.create(rfid="DELETEME01")
        label = tag.label_id
        tag.delete()
        self.assertFalse(RFID.objects.filter(label_id=label).exists())
        self.assertTrue(
            RFID.all_objects.filter(label_id=label, is_deleted=True).exists()
        )

        resource = RFIDResource()
        headers = resource.get_export_headers()
        dataset = Dataset()
        dataset.headers = headers
        row = {header: "" for header in headers}
        row.update(
            {
                "label_id": str(label),
                "rfid": "DELETEME02",
                "allowed": "true",
                "color": RFID.BLACK,
                "kind": RFID.CLASSIC,
            }
        )
        dataset.append([row[h] for h in headers])

        result = resource.import_data(dataset, dry_run=False)

        self.assertFalse(result.has_errors())
        restored = RFID.all_objects.get(label_id=label)
        self.assertEqual(restored.rfid, "DELETEME02")
        self.assertFalse(restored.is_deleted)

        account = CustomerAccount.objects.get(name="IMPORTED ADMIN ACCOUNT")
        tag = RFID.objects.get(rfid="NAMETAG002")
        self.assertTrue(tag.energy_accounts.filter(pk=account.pk).exists())


class CheckRFIDCommandTests(TestCase):
    def test_successful_validation_outputs_json(self):
        out = StringIO()

        call_command("check_rfid", "abcd1234", stdout=out)

        payload = json.loads(out.getvalue())
        self.assertEqual(payload["rfid"], "ABCD1234")
        self.assertTrue(payload["created"])
        self.assertTrue(RFID.objects.filter(rfid="ABCD1234").exists())

    def test_invalid_value_raises_error(self):
        with self.assertRaises(CommandError):
            call_command("check_rfid", "invalid!")

    def test_kind_option_updates_existing_tag(self):
        tag = RFID.objects.create(rfid="EXISTING", allowed=False, kind=RFID.CLASSIC)
        out = StringIO()

        call_command(
            "check_rfid",
            "existing",
            "--kind",
            RFID.NTAG215,
            stdout=out,
        )

        payload = json.loads(out.getvalue())
        tag.refresh_from_db()
        self.assertFalse(payload["created"])
        self.assertEqual(payload["kind"], RFID.NTAG215)
        self.assertEqual(tag.kind, RFID.NTAG215)


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


class ReleaseProcessTests(TestCase):
    def setUp(self):
        self.package = Package.objects.create(name="pkg")
        self.release = PackageRelease.objects.create(
            package=self.package, version="1.0.0"
        )

    @mock.patch("core.views._collect_dirty_files")
    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.release_utils._git_clean", return_value=False)
    def test_step_check_requires_clean_repo(
        self, git_clean, sync_main, collect_dirty
    ):
        collect_dirty.return_value = [
            {"path": "core/models.py", "status": "M", "status_label": "Modified"}
        ]
        ctx: dict = {}
        with self.assertRaises(core_views.DirtyRepository):
            _step_check_version(self.release, ctx, Path("rel.log"))
        self.assertEqual(
            ctx["dirty_files"],
            [
                {
                    "path": "core/models.py",
                    "status": "M",
                    "status_label": "Modified",
                }
            ],
        )
        sync_main.assert_called_once_with(Path("rel.log"))

    def test_step_check_todos_logs_instruction_when_pending(self):
        log_path = Path("rel.log")
        log_path.unlink(missing_ok=True)
        Todo.objects.create(request="Review checklist")
        ctx: dict[str, object] = {}

        try:
            with self.assertRaises(core_views.PendingTodos):
                core_views._step_check_todos(self.release, ctx, log_path)

            contents = log_path.read_text(encoding="utf-8")
            message = "Release checklist requires acknowledgment before continuing."
            self.assertIn(message, contents)
            self.assertIn("Review outstanding TODO items", contents)

            with self.assertRaises(core_views.PendingTodos):
                core_views._step_check_todos(self.release, ctx, log_path)

            contents = log_path.read_text(encoding="utf-8")
            self.assertEqual(contents.count(message), 1)
        finally:
            log_path.unlink(missing_ok=True)

    def test_step_check_todos_auto_ack_when_no_pending(self):
        log_path = Path("rel.log")
        log_path.unlink(missing_ok=True)
        ctx: dict[str, object] = {}

        try:
            with mock.patch("core.views._refresh_changelog_once"):
                core_views._step_check_todos(self.release, ctx, log_path)
        finally:
            log_path.unlink(missing_ok=True)

        self.assertTrue(ctx.get("todos_ack"))
        self.assertNotIn("todos_required", ctx)
        self.assertIsNone(ctx.get("todos"))

    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.release_utils._git_clean", return_value=True)
    @mock.patch("core.views.release_utils.network_available", return_value=False)
    def test_step_check_keeps_repo_clean(
        self, network_available, git_clean, sync_main
    ):
        version_path = Path("VERSION")
        original = version_path.read_text(encoding="utf-8")
        _step_check_version(self.release, {}, Path("rel.log"))
        proc = subprocess.run(
            ["git", "status", "--porcelain", str(version_path)],
            capture_output=True,
            text=True,
        )
        self.assertFalse(proc.stdout.strip())
        self.assertEqual(version_path.read_text(encoding="utf-8"), original)
        sync_main.assert_called_once_with(Path("rel.log"))

    @mock.patch("core.views.requests.get")
    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.release_utils.network_available", return_value=True)
    @mock.patch("core.views.release_utils._git_clean", return_value=True)
    def test_step_check_ignores_yanked_release(
        self, git_clean, network_available, sync_main, requests_get
    ):
        response = mock.Mock()
        response.ok = True
        response.json.return_value = {
            "releases": {
                "0.1.12": [
                    {"filename": "pkg.whl", "yanked": True},
                    {"filename": "pkg.tar.gz", "yanked": True},
                ]
            }
        }
        requests_get.return_value = response
        self.release.version = "0.1.12"
        _step_check_version(self.release, {}, Path("rel.log"))
        requests_get.assert_called_once()
        sync_main.assert_called_once_with(Path("rel.log"))

    @mock.patch("core.views.requests.get")
    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.release_utils.network_available", return_value=True)
    @mock.patch("core.views.release_utils._git_clean", return_value=True)
    def test_step_check_blocks_available_release(
        self, git_clean, network_available, sync_main, requests_get
    ):
        response = mock.Mock()
        response.ok = True
        response.json.return_value = {
            "releases": {
                "0.1.12": [
                    {"filename": "pkg.whl", "yanked": False},
                    {"filename": "pkg.tar.gz"},
                ]
            }
        }
        requests_get.return_value = response
        self.release.version = "0.1.12"
        with self.assertRaises(Exception) as exc:
            _step_check_version(self.release, {}, Path("rel.log"))
        self.assertIn("already on PyPI", str(exc.exception))
        requests_get.assert_called_once()
        sync_main.assert_called_once_with(Path("rel.log"))

    @mock.patch("core.views.release_utils.network_available", return_value=False)
    @mock.patch("core.views._collect_dirty_files")
    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.subprocess.run")
    @mock.patch("core.views.release_utils._git_clean", return_value=False)
    def test_step_check_commits_release_prep_changes(
        self,
        git_clean,
        subprocess_run,
        sync_main,
        collect_dirty,
        network_available,
    ):
        fixture_path = next(Path("core/fixtures").glob("releases__*.json"))
        collect_dirty.return_value = [
            {
                "path": str(fixture_path),
                "status": "M",
                "status_label": "Modified",
            },
            {"path": "CHANGELOG.rst", "status": "M", "status_label": "Modified"},
        ]
        subprocess_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")

        ctx: dict[str, object] = {}
        _step_check_version(self.release, ctx, Path("rel.log"))

        add_call = mock.call(
            ["git", "add", str(fixture_path), "CHANGELOG.rst"],
            check=True,
        )
        commit_call = mock.call(
            [
                "git",
                "commit",
                "-m",
                "chore: sync release fixtures and changelog",
            ],
            check=True,
        )
        self.assertIn(add_call, subprocess_run.call_args_list)
        self.assertIn(commit_call, subprocess_run.call_args_list)
        self.assertNotIn("dirty_files", ctx)

    @mock.patch("core.views.release_utils.network_available", return_value=False)
    @mock.patch("core.views._collect_dirty_files")
    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.subprocess.run")
    @mock.patch("core.views.release_utils._git_clean", return_value=False)
    def test_step_check_commits_changelog_only(
        self,
        git_clean,
        subprocess_run,
        sync_main,
        collect_dirty,
        network_available,
    ):
        collect_dirty.return_value = [
            {"path": "CHANGELOG.rst", "status": "M", "status_label": "Modified"}
        ]
        subprocess_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")

        ctx: dict[str, object] = {}
        _step_check_version(self.release, ctx, Path("rel.log"))

        subprocess_run.assert_any_call(
            ["git", "add", "CHANGELOG.rst"], check=True
        )
        subprocess_run.assert_any_call(
            ["git", "commit", "-m", "docs: refresh changelog"], check=True
        )
        self.assertNotIn("dirty_files", ctx)

    @mock.patch("core.models.PackageRelease.dump_fixture")
    def test_save_does_not_dump_fixture(self, dump):
        self.release.pypi_url = "https://example.com"
        self.release.save()
        dump.assert_not_called()

    @mock.patch("core.views.subprocess.run")
    @mock.patch("core.views.PackageRelease.dump_fixture")
    @mock.patch("core.views.release_utils.promote", side_effect=Exception("boom"))
    def test_promote_cleans_repo_on_failure(self, promote, dump_fixture, run):
        import subprocess as sp

        def fake_run(cmd, check=True, capture_output=False, text=False):
            if capture_output:
                stdout = ""
                if cmd[:3] == ["git", "rev-parse", "origin/main"]:
                    stdout = "abc123\n"
                elif cmd[:4] == ["git", "merge-base", "HEAD", "origin/main"]:
                    stdout = "abc123\n"
                return sp.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
            return sp.CompletedProcess(cmd, 0)

        run.side_effect = fake_run
        with self.assertRaises(Exception):
            _step_promote_build(self.release, {}, Path("rel.log"))
        dump_fixture.assert_not_called()
        run.assert_any_call(["git", "reset", "--hard"], check=False)
        run.assert_any_call(["git", "clean", "-fd"], check=False)

    @mock.patch("core.views.PackageRelease.dump_fixture")
    @mock.patch("core.views._sync_with_origin_main")
    @mock.patch("core.views.subprocess.run")
    def test_pre_release_syncs_with_main(
        self, run, sync_main, dump_fixture
    ):
        import subprocess as sp

        def fake_run(cmd, check=True, capture_output=False, text=False):
            if capture_output:
                return sp.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[:2] == ["git", "diff"]:
                return sp.CompletedProcess(cmd, 1)
            return sp.CompletedProcess(cmd, 0)

        run.side_effect = fake_run
        version_path = Path("VERSION")
        original_version = version_path.read_text(encoding="utf-8")

        try:
            _step_pre_release_actions(self.release, {}, Path("rel.log"))
        finally:
            version_path.write_text(original_version, encoding="utf-8")

        sync_main.assert_called_once_with(Path("rel.log"))
        release_fixtures = sorted(
            str(path) for path in Path("core/fixtures").glob("releases__*.json")
        )
        if release_fixtures:
            run.assert_any_call(["git", "add", *release_fixtures], check=True)
        run.assert_any_call(["git", "add", "CHANGELOG.rst"], check=True)
        run.assert_any_call(["git", "add", "VERSION"], check=True)
        run.assert_any_call(["git", "diff", "--cached", "--quiet"], check=False)
        ensure_todo.assert_called_once_with(self.release, previous_version=mock.ANY)

    @mock.patch("core.views.subprocess.run")
    @mock.patch("core.views.PackageRelease.dump_fixture")
    @mock.patch("core.views.release_utils.promote")
    def test_promote_verifies_origin_and_pushes_main(self, promote, dump_fixture, run):
        import subprocess as sp

        def fake_run(cmd, check=True, capture_output=False, text=False):
            if capture_output:
                stdout = ""
                if cmd[:3] == ["git", "rev-parse", "origin/main"]:
                    stdout = "abc123\n"
                elif cmd[:4] == ["git", "merge-base", "HEAD", "origin/main"]:
                    stdout = "abc123\n"
                return sp.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
            return sp.CompletedProcess(cmd, 0)

        run.side_effect = fake_run
        _step_promote_build(self.release, {}, Path("rel.log"))
        run.assert_any_call(["git", "fetch", "origin", "main"], check=True)
        run.assert_any_call(
            ["git", "rev-parse", "origin/main"],
            check=True,
            capture_output=True,
            text=True,
        )
        run.assert_any_call(
            ["git", "merge-base", "HEAD", "origin/main"],
            check=True,
            capture_output=True,
            text=True,
        )
        run.assert_any_call(["git", "push"], check=True)

    @mock.patch("core.views.subprocess.run")
    @mock.patch("core.views.PackageRelease.dump_fixture")
    @mock.patch("core.views.release_utils.promote")
    def test_promote_aborts_if_origin_advances(self, promote, dump_fixture, run):
        import subprocess as sp

        def fake_run(cmd, check=True, capture_output=False, text=False):
            if capture_output:
                if cmd[:3] == ["git", "rev-parse", "origin/main"]:
                    return sp.CompletedProcess(cmd, 0, stdout="new\n", stderr="")
                if cmd[:4] == ["git", "merge-base", "HEAD", "origin/main"]:
                    return sp.CompletedProcess(cmd, 0, stdout="old\n", stderr="")
                return sp.CompletedProcess(cmd, 0, stdout="", stderr="")
            return sp.CompletedProcess(cmd, 0)

        run.side_effect = fake_run

        with self.assertRaises(Exception):
            _step_promote_build(self.release, {}, Path("rel.log"))

        promote.assert_not_called()
        run.assert_any_call(["git", "reset", "--hard"], check=False)
        run.assert_any_call(["git", "clean", "-fd"], check=False)

    @mock.patch("core.views.subprocess.run")
    @mock.patch("core.views.PackageRelease.dump_fixture")
    def test_promote_advances_version(self, dump_fixture, run):
        import subprocess as sp

        def fake_run(cmd, check=True, capture_output=False, text=False):
            if capture_output:
                stdout = ""
                if cmd[:3] == ["git", "rev-parse", "origin/main"]:
                    stdout = "abc123\n"
                elif cmd[:4] == ["git", "merge-base", "HEAD", "origin/main"]:
                    stdout = "abc123\n"
                return sp.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
            return sp.CompletedProcess(cmd, 0)

        run.side_effect = fake_run

        version_path = Path("VERSION")
        original = version_path.read_text(encoding="utf-8")
        version_path.write_text("0.0.1\n", encoding="utf-8")

        def fake_promote(*args, **kwargs):
            version_path.write_text(self.release.version + "\n", encoding="utf-8")

        with mock.patch("core.views.release_utils.promote", side_effect=fake_promote):
            _step_promote_build(self.release, {}, Path("rel.log"))

        self.assertEqual(
            version_path.read_text(encoding="utf-8"),
            self.release.version + "\n",
        )
        version_path.write_text(original, encoding="utf-8")

    @mock.patch("core.views.timezone.now")
    @mock.patch("core.views.PackageRelease.dump_fixture")
    @mock.patch("core.views.release_utils.publish")
    def test_publish_sets_pypi_url(self, publish, dump_fixture, now):
        now.return_value = datetime(2025, 3, 4, 5, 6, tzinfo=datetime_timezone.utc)
        publish.return_value = ["PyPI"]
        _step_publish(self.release, {}, Path("rel.log"))
        self.release.refresh_from_db()
        self.assertEqual(
            self.release.pypi_url,
            f"https://pypi.org/project/{self.package.name}/{self.release.version}/",
        )
        self.assertEqual(self.release.github_url, "")
        self.assertEqual(
            self.release.release_on,
            datetime(2025, 3, 4, 5, 6, tzinfo=datetime_timezone.utc),
        )
        dump_fixture.assert_called_once()
        publish.assert_called_once()
        kwargs = publish.call_args.kwargs
        self.assertIn("repositories", kwargs)
        repositories = kwargs["repositories"]
        self.assertEqual(len(repositories), 1)
        self.assertEqual(repositories[0].name, "PyPI")

    @mock.patch("core.views.PackageRelease.dump_fixture")
    @mock.patch("core.views.release_utils.publish", side_effect=Exception("boom"))
    def test_publish_failure_keeps_url_blank(self, publish, dump_fixture):
        with self.assertRaises(Exception):
            _step_publish(self.release, {}, Path("rel.log"))
        self.release.refresh_from_db()
        self.assertEqual(self.release.pypi_url, "")
        self.assertEqual(self.release.github_url, "")
        self.assertIsNone(self.release.release_on)
        dump_fixture.assert_not_called()

    @mock.patch("core.views.timezone.now")
    @mock.patch("core.views.PackageRelease.dump_fixture")
    @mock.patch("core.views.release_utils.publish")
    def test_publish_records_github_url_when_configured(
        self, publish, dump_fixture, now
    ):
        now.return_value = datetime(2025, 3, 4, 6, 7, tzinfo=datetime_timezone.utc)
        user = User.objects.create_superuser("release-owner", "owner@example.com", "pw")
        manager = ReleaseManager.objects.create(
            user=user,
            pypi_username="octocat",
            pypi_token="primary-token",
            github_token="gh-token",
            secondary_pypi_url="https://upload.github.com/pypi/",
        )
        self.release.release_manager = manager
        self.release.save(update_fields=["release_manager"])
        self.package.repository_url = "https://github.com/example/project"
        self.package.save(update_fields=["repository_url"])
        publish.return_value = ["PyPI", "GitHub Packages"]

        _step_publish(self.release, {}, Path("rel.log"))

        self.release.refresh_from_db()
        self.assertTrue(self.release.github_url)
        self.assertIn("github.com/example/project", self.release.github_url)
        args, kwargs = publish.call_args
        repositories = kwargs.get("repositories")
        self.assertEqual(len(repositories), 2)
        self.assertEqual(repositories[0].name, "PyPI")
        self.assertEqual(repositories[1].name, "GitHub Packages")

    @mock.patch("core.views.subprocess.run")
    @mock.patch("core.views._sync_with_origin_main")
    def test_pre_release_actions_skipped_in_dry_run(self, sync_main, run):
        log_path = Path("rel.log")
        if log_path.exists():
            log_path.unlink()

        try:
            _step_pre_release_actions(self.release, {"dry_run": True}, log_path)
            self.assertTrue(log_path.exists())
            contents = log_path.read_text(encoding="utf-8")
            self.assertIn("Dry run: skipping pre-release actions", contents)
        finally:
            if log_path.exists():
                log_path.unlink()

        sync_main.assert_not_called()
        run.assert_not_called()

    @mock.patch("core.views.release_utils.promote")
    def test_promote_build_skipped_in_dry_run(self, promote):
        log_path = Path("rel.log")
        if log_path.exists():
            log_path.unlink()

        try:
            _step_promote_build(self.release, {"dry_run": True}, log_path)
            self.assertTrue(log_path.exists())
            contents = log_path.read_text(encoding="utf-8")
            self.assertIn("Dry run: skipping build promotion", contents)
        finally:
            if log_path.exists():
                log_path.unlink()

        promote.assert_not_called()

    @mock.patch("core.views.release_utils.publish")
    @mock.patch("core.views.PackageRelease.dump_fixture")
    def test_publish_uses_test_repository_in_dry_run(self, dump_fixture, publish):
        log_path = Path("rel.log")
        if log_path.exists():
            log_path.unlink()

        publish.return_value = ["Test PyPI"]
        env = {
            "PYPI_TEST_REPOSITORY_URL": "https://test.example/simple/",
            "PYPI_TEST_API_TOKEN": "token",
        }

        with mock.patch.dict(os.environ, env, clear=False):
            _step_publish(self.release, {"dry_run": True}, log_path)

        self.release.refresh_from_db()
        self.assertEqual(self.release.pypi_url, "")
        self.assertEqual(self.release.github_url, "")
        self.assertIsNone(self.release.release_on)
        dump_fixture.assert_not_called()
        publish.assert_called_once()
        repositories = publish.call_args.kwargs["repositories"]
        self.assertEqual(len(repositories), 1)
        target = repositories[0]
        self.assertEqual(target.name, "Test PyPI")
        self.assertEqual(target.repository_url, "https://test.example/simple/")
        self.assertFalse(target.verify_availability)
        self.assertTrue(log_path.exists())
        contents = log_path.read_text(encoding="utf-8")
        self.assertIn("Dry run: uploading distribution", contents)
        self.assertIn("Dry run: skipped release metadata updates", contents)
        log_path.unlink(missing_ok=True)

    def test_release_progress_toggle_dry_run_before_start(self):
        user = User.objects.create_superuser("admin", "admin@example.com", "pw")
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self.client.force_login(user)

        response = self.client.get(f"{url}?set_dry_run=1&dry_run=1", follow=True)

        self.assertEqual(response.status_code, 200)
        context = response.context
        if isinstance(context, list):
            context = context[-1]
        self.assertTrue(context["dry_run"])
        self.assertTrue(context["dry_run_toggle_enabled"])
        session = self.client.session
        ctx = session.get(f"release_publish_{self.release.pk}")
        self.assertTrue(ctx.get("dry_run"))

    def test_release_progress_toggle_blocked_while_running(self):
        user = User.objects.create_superuser("admin", "admin@example.com", "pw")
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self.client.force_login(user)
        session = self.client.session
        session[f"release_publish_{self.release.pk}"] = {
            "step": 0,
            "started": True,
            "paused": False,
        }
        session.save()

        response = self.client.get(f"{url}?set_dry_run=1&dry_run=1")
        self.assertEqual(response.status_code, 302)
        session = self.client.session
        ctx = session.get(f"release_publish_{self.release.pk}")
        self.assertFalse(ctx.get("dry_run"))

        follow = self.client.get(url)
        follow_context = follow.context
        if isinstance(follow_context, list):
            follow_context = follow_context[-1]
        self.assertFalse(follow_context["dry_run"])
        self.assertFalse(follow_context["dry_run_toggle_enabled"])

    def test_start_request_sets_dry_run_flag(self):
        user = User.objects.create_superuser("admin", "admin@example.com", "pw")
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self.client.force_login(user)

        self.client.get(f"{url}?start=1&dry_run=1")

        session = self.client.session
        ctx = session.get(f"release_publish_{self.release.pk}")
        self.assertTrue(ctx.get("dry_run"))

    def test_resume_button_shown_when_credentials_missing(self):
        user = User.objects.create_superuser("admin", "admin@example.com", "pw")
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        self.client.force_login(user)

        self.client.get(f"{url}?start=1")

        session = self.client.session
        ctx = session.get(f"release_publish_{self.release.pk}") or {}
        ctx.update({"step": 7, "started": True, "paused": False})
        session[f"release_publish_{self.release.pk}"] = ctx
        session.save()

        response = self.client.get(f"{url}?step=7")
        self.assertEqual(response.status_code, 200)
        context = response.context
        if isinstance(context, list):
            context = context[-1]
        self.assertTrue(context["resume_available"])
        self.assertIn(b"Resume Publish", response.content)

    def test_resume_without_step_parameter_defaults_to_current_progress(self):
        run: list[str] = []

        def step_fn(release, ctx, log_path):
            run.append("step")

        steps = [("Only step", step_fn)]
        user = User.objects.create_superuser("admin", "admin@example.com", "pw")
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        with mock.patch("core.views.PUBLISH_STEPS", steps):
            self.client.force_login(user)
            session = self.client.session
            session[f"release_publish_{self.release.pk}"] = {
                "step": 0,
                "started": True,
                "paused": False,
            }
            session.save()

            response = self.client.get(f"{url}?resume=1")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(run, ["step"])

            session = self.client.session
            ctx = session.get(f"release_publish_{self.release.pk}")
            self.assertEqual(ctx.get("step"), 1)

    def test_new_todo_does_not_reset_pending_flow(self):
        user = User.objects.create_superuser("admin", "admin@example.com", "pw")
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        Todo.objects.create(request="Initial checklist item")
        steps = [("Confirm release TODO completion", core_views._step_check_todos)]
        with mock.patch("core.views.PUBLISH_STEPS", steps):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertTrue(response.context["has_pending_todos"])
            self.client.get(f"{url}?ack_todos=1")
            self.client.get(f"{url}?start=1")
            self.client.get(f"{url}?step=0")
            Todo.objects.create(request="Follow-up checklist item")
            response = self.client.get(url)
            self.assertEqual(
                Todo.objects.filter(is_deleted=False, done_on__isnull=True).count(),
                1,
            )
            self.assertIsNone(response.context["todos"])
            self.assertFalse(response.context["has_pending_todos"])
            session = self.client.session
            ctx = session.get(f"release_publish_{self.release.pk}")
            self.assertTrue(ctx.get("todos_ack"))

    def test_release_progress_uses_lockfile(self):
        run = []

        def step1(release, ctx, log_path):
            run.append("step1")

        def step2(release, ctx, log_path):
            run.append("step2")

        steps = [("One", step1), ("Two", step2)]
        user = User.objects.create_superuser("admin", "admin@example.com", "pw")
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        with mock.patch("core.views.PUBLISH_STEPS", steps):
            self.client.force_login(user)
            self.client.get(f"{url}?step=0")
            self.assertEqual(run, ["step1"])
            client2 = Client()
            client2.force_login(user)
            client2.get(f"{url}?step=1")
            self.assertEqual(run, ["step1", "step2"])
            lock_file = Path("locks") / f"release_publish_{self.release.pk}.json"
            self.assertFalse(lock_file.exists())

    def test_release_progress_restart(self):
        run = []

        def step_fail(release, ctx, log_path):
            run.append("step")
            raise Exception("boom")

        steps = [("Fail", step_fail)]
        user = User.objects.create_superuser("admin", "admin@example.com", "pw")
        url = reverse("release-progress", args=[self.release.pk, "publish"])
        count_file = Path("locks") / f"release_publish_{self.release.pk}.restarts"
        if count_file.exists():
            count_file.unlink()
        with mock.patch("core.views.PUBLISH_STEPS", steps):
            self.client.force_login(user)
            self.assertFalse(count_file.exists())
            self.client.get(f"{url}?step=0")
            self.client.get(f"{url}?step=0")
            self.assertEqual(run, ["step"])
            self.assertFalse(count_file.exists())
            self.client.get(f"{url}?restart=1")
            self.assertTrue(count_file.exists())
            self.assertEqual(count_file.read_text(), "1")
            self.client.get(f"{url}?step=0")
            self.assertEqual(run, ["step", "step"])
            self.client.get(f"{url}?restart=1")
            # Restart counter resets after running a step
            self.assertEqual(count_file.read_text(), "1")


class PackageReleaseFixtureTests(TestCase):
    def setUp(self):
        self.base = Path("core/fixtures")
        self.existing_fixtures = {
            path: path.read_text(encoding="utf-8")
            for path in self.base.glob("releases__*.json")
        }
        self.addCleanup(self._restore_fixtures)

        self.package = Package.objects.create(name="fixture-pkg")
        self.release_one = PackageRelease.objects.create(
            package=self.package,
            version="9.9.9",
        )
        self.release_two = PackageRelease.objects.create(
            package=self.package,
            version="9.9.10",
        )

    def _restore_fixtures(self):
        current_paths = set(self.base.glob("releases__*.json"))
        for path in current_paths:
            if path not in self.existing_fixtures:
                path.unlink()
        for path, content in self.existing_fixtures.items():
            path.write_text(content, encoding="utf-8")

    def test_dump_fixture_only_writes_changed_releases(self):
        PackageRelease.dump_fixture()
        target_one = self.base / "releases__packagerelease_9_9_9.json"
        target_two = self.base / "releases__packagerelease_9_9_10.json"

        self.assertTrue(target_one.exists())
        self.assertTrue(target_two.exists())

        self.release_two.changelog = "updated notes"
        self.release_two.save(update_fields=["changelog"])

        original_write = Path.write_text
        written_paths: list[Path] = []

        def tracking_write_text(path_obj, data, *args, **kwargs):
            written_paths.append(path_obj)
            return original_write(path_obj, data, *args, **kwargs)

        with mock.patch("pathlib.Path.write_text", tracking_write_text):
            PackageRelease.dump_fixture()

        written_set = set(written_paths)
        self.assertNotIn(target_one, written_set)
        self.assertIn(target_two, written_set)
        self.assertIn("updated notes", target_two.read_text(encoding="utf-8"))

    def test_dump_fixture_removes_missing_release_files(self):
        PackageRelease.dump_fixture()
        target_two = self.base / "releases__packagerelease_9_9_10.json"
        self.assertTrue(target_two.exists())

        self.release_two.delete()

        PackageRelease.dump_fixture()

        self.assertFalse(target_two.exists())


class ReleaseProgressSyncTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(self.user)
        self.package = Package.objects.get(name="arthexis")
        self.version_path = Path("VERSION")
        self.original_version = self.version_path.read_text(encoding="utf-8")
        self.version_path.write_text("1.2.3", encoding="utf-8")

    def tearDown(self):
        self.version_path.write_text(self.original_version, encoding="utf-8")

    @mock.patch("core.views.PackageRelease.dump_fixture")
    @mock.patch("core.views.revision.get_revision", return_value="abc123")
    def test_unpublished_release_syncs_version_and_revision(
        self, get_revision, dump_fixture
    ):
        release = PackageRelease.objects.create(
            package=self.package,
            version="1.0.0",
        )
        release.revision = "oldrev"
        release.save(update_fields=["revision"])

        url = reverse("release-progress", args=[release.pk, "publish"])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        release.refresh_from_db()
        self.assertEqual(release.version, "1.2.4")
        self.assertEqual(release.revision, "abc123")
        dump_fixture.assert_called_once()

    def test_published_release_not_current_shows_conflict_message(self):
        release = PackageRelease.objects.create(
            package=self.package,
            version="1.2.4",
            pypi_url="https://example.com",
        )

        url = reverse("release-progress", args=[release.pk, "publish"])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "already published")

    @override_settings(DEBUG=True)
    def test_published_release_not_current_includes_debug_details(self):
        release = PackageRelease.objects.create(
            package=self.package,
            version="1.2.4",
            pypi_url="https://example.com",
        )

        url = reverse("release-progress", args=[release.pk, "publish"])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Debug details")
        self.assertContains(response, "release_version")


class ReleaseProgressFixtureVisibilityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            "fixture-check", "fixture@example.com", "pw"
        )
        self.client.force_login(self.user)
        current_version = Path("VERSION").read_text(encoding="utf-8").strip()
        package = Package.objects.filter(is_active=True).first()
        if package is None:
            package = Package.objects.create(name="fixturepkg", is_active=True)
        try:
            self.release = PackageRelease.objects.get(
                package=package, version=current_version
            )
        except PackageRelease.DoesNotExist:
            self.release = PackageRelease.objects.create(
                package=package, version=current_version
            )
        self.session_key = f"release_publish_{self.release.pk}"
        self.log_name = core_views._release_log_name(
            self.release.package.name, self.release.version
        )
        self.lock_path = Path("locks") / f"{self.session_key}.json"
        self.restart_path = Path("locks") / f"{self.session_key}.restarts"
        self.log_path = Path("logs") / self.log_name
        for path in (self.lock_path, self.restart_path, self.log_path):
            if path.exists():
                path.unlink()
        try:
            self.fixture_step_index = next(
                idx
                for idx, (name, _) in enumerate(core_views.PUBLISH_STEPS)
                if name == core_views.FIXTURE_REVIEW_STEP_NAME
            )
        except StopIteration:  # pragma: no cover - defensive guard
            self.fail("Fixture review step not configured in publish steps")
        self.url = reverse("release-progress", args=[self.release.pk, "publish"])

    def tearDown(self):
        session = self.client.session
        if self.session_key in session:
            session.pop(self.session_key)
            session.save()
        for path in (self.lock_path, self.restart_path, self.log_path):
            if path.exists():
                path.unlink()
        super().tearDown()

    def _set_session(self, step: int, fixtures: list[dict]):
        session = self.client.session
        session[self.session_key] = {
            "step": step,
            "fixtures": fixtures,
            "log": self.log_name,
            "started": True,
        }
        session.save()

    def test_fixture_summary_visible_until_migration_step(self):
        fixtures = [
            {
                "path": "core/fixtures/example.json",
                "count": 2,
                "models": ["core.Model"],
            }
        ]
        self._set_session(self.fixture_step_index, fixtures)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["fixtures"], fixtures)
        self.assertContains(response, "Fixture changes")

    def test_fixture_summary_hidden_after_migration_step(self):
        fixtures = [
            {
                "path": "core/fixtures/example.json",
                "count": 2,
                "models": ["core.Model"],
            }
        ]
        self._set_session(self.fixture_step_index + 1, fixtures)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["fixtures"])
        self.assertNotContains(response, "Fixture changes")


class PackageReleaseAdminActionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = PackageReleaseAdmin(PackageRelease, self.site)
        self.messages = []

        def _capture_message(request, message, level=messages.INFO):
            self.messages.append((message, level))

        self.admin.message_user = _capture_message
        self.package = Package.objects.create(name="pkg")
        self.package.is_active = True
        self.package.save(update_fields=["is_active"])
        self.release = PackageRelease.objects.create(
            package=self.package,
            version="1.0.0",
            pypi_url="https://pypi.org/project/pkg/1.0.0/",
        )
        self.request = self.factory.get("/")

    @mock.patch("core.admin.PackageRelease.dump_fixture")
    @mock.patch("core.admin.requests.get")
    def test_validate_deletes_missing_release(self, mock_get, dump):
        mock_get.return_value.status_code = 404
        self.admin.validate_releases(self.request, PackageRelease.objects.all())
        self.assertEqual(PackageRelease.objects.count(), 0)
        dump.assert_called_once()

    @mock.patch("core.admin.PackageRelease.dump_fixture")
    @mock.patch("core.admin.requests.get")
    def test_validate_keeps_existing_release(self, mock_get, dump):
        mock_get.return_value.status_code = 200
        self.admin.validate_releases(self.request, PackageRelease.objects.all())
        self.assertEqual(PackageRelease.objects.count(), 1)
        dump.assert_not_called()

    @mock.patch("core.admin.PackageRelease.dump_fixture")
    @mock.patch("core.admin.requests.get")
    def test_refresh_from_pypi_reports_missing_releases(self, mock_get, dump):
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "releases": {
                "1.0.0": [
                    {"upload_time_iso_8601": "2024-01-01T12:30:00.000000Z"}
                ],
                "1.1.0": [
                    {"upload_time_iso_8601": "2024-02-02T15:45:00.000000Z"}
                ],
            }
        }
        self.admin.refresh_from_pypi(self.request, PackageRelease.objects.none())
        self.assertFalse(
            PackageRelease.objects.filter(version="1.1.0").exists()
        )
        dump.assert_not_called()
        self.assertIn(
            (
                "Manual creation required for 1 release: 1.1.0",
                messages.WARNING,
            ),
            self.messages,
        )

    @mock.patch("core.admin.PackageRelease.dump_fixture")
    @mock.patch("core.admin.requests.get")
    def test_refresh_from_pypi_updates_release_date(self, mock_get, dump):
        self.release.release_on = None
        self.release.save(update_fields=["release_on"])
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "releases": {
                "1.0.0": [
                    {"upload_time_iso_8601": "2024-01-01T12:30:00.000000Z"}
                ]
            }
        }
        self.admin.refresh_from_pypi(self.request, PackageRelease.objects.none())
        self.release.refresh_from_db()
        self.assertEqual(
            self.release.release_on,
            datetime(2024, 1, 1, 12, 30, tzinfo=datetime_timezone.utc),
        )
        dump.assert_called_once()

    @mock.patch("core.admin.PackageRelease.dump_fixture")
    @mock.patch("core.admin.requests.get")
    def test_refresh_from_pypi_restores_deleted_release(self, mock_get, dump):
        self.release.is_deleted = True
        self.release.save(update_fields=["is_deleted"])
        mock_get.return_value.raise_for_status.return_value = None
        mock_get.return_value.json.return_value = {
            "releases": {
                "1.0.0": [
                    {"upload_time_iso_8601": "2024-01-01T12:30:00.000000Z"}
                ]
            }
        }

        self.admin.refresh_from_pypi(self.request, PackageRelease.objects.none())

        self.assertTrue(
            PackageRelease.objects.filter(version="1.0.0").exists()
        )
        dump.assert_called_once()

    @mock.patch("core.admin.release_utils.check_pypi_readiness")
    def test_test_pypi_connection_action_reports_messages(self, check):
        check.return_value = types.SimpleNamespace(
            ok=True,
            messages=[
                ("success", "Twine available"),
                ("warning", "Offline mode enabled; skipping network connectivity checks"),
            ],
        )

        self.admin.test_pypi_connection(
            self.request, PackageRelease.objects.filter(pk=self.release.pk)
        )

        self.assertIn(
            (f"{self.release}: Twine available", messages.SUCCESS),
            self.messages,
        )
        self.assertIn(
            (
                f"{self.release}: Offline mode enabled; skipping network connectivity checks",
                messages.WARNING,
            ),
            self.messages,
        )
        self.assertIn(
            (f"{self.release}: PyPI connectivity check passed", messages.SUCCESS),
            self.messages,
        )

    @mock.patch("core.admin.release_utils.check_pypi_readiness")
    def test_test_pypi_connection_handles_errors(self, check):
        check.return_value = types.SimpleNamespace(
            ok=False, messages=[("error", "Missing PyPI credentials")]
        )

        self.admin.test_pypi_connection(
            self.request, PackageRelease.objects.filter(pk=self.release.pk)
        )

        self.assertIn(
            (f"{self.release}: Missing PyPI credentials", messages.ERROR),
            self.messages,
        )
        self.assertNotIn(
            (f"{self.release}: PyPI connectivity check passed", messages.SUCCESS),
            self.messages,
        )

    @mock.patch("core.admin.release_utils.check_pypi_readiness")
    def test_test_pypi_connection_action_button(self, check):
        check.return_value = types.SimpleNamespace(
            ok=True, messages=[("success", "Twine available")]
        )

        self.admin.test_pypi_connection_action(self.request, self.release)

        self.assertIn(
            (f"{self.release}: PyPI connectivity check passed", messages.SUCCESS),
            self.messages,
        )

    def test_test_pypi_connection_requires_selection(self):
        self.admin.test_pypi_connection(self.request, PackageRelease.objects.none())

        self.assertIn(
            ("Select at least one release to test", messages.ERROR), self.messages
        )


class PackageActiveTests(TestCase):
    def test_only_one_active_package(self):
        default = Package.objects.get(name="arthexis")
        self.assertTrue(default.is_active)
        other = Package.objects.create(name="pkg", is_active=True)
        default.refresh_from_db()
        other.refresh_from_db()
        self.assertFalse(default.is_active)
        self.assertTrue(other.is_active)


class PackageReleaseCurrentTests(TestCase):
    def setUp(self):
        self.package = Package.objects.get(name="arthexis")
        self.version_path = Path("VERSION")
        self.original = self.version_path.read_text()
        self.version_path.write_text("1.0.0")
        self.release = PackageRelease.objects.create(
            package=self.package, version="1.0.0"
        )

    def tearDown(self):
        self.version_path.write_text(self.original)

    def test_is_current_true_when_version_matches_and_package_active(self):
        self.assertTrue(self.release.is_current)

    def test_is_current_false_when_package_inactive(self):
        self.package.is_active = False
        self.package.save()
        self.assertFalse(self.release.is_current)

    def test_is_current_false_when_version_has_plus(self):
        self.version_path.write_text("1.0.0+")
        self.assertFalse(self.release.is_current)


class PackageReleaseRevisionTests(TestCase):
    def setUp(self):
        self.package = Package.objects.get(name="arthexis")
        self.release = PackageRelease.objects.create(
            package=self.package,
            version="1.0.0",
            revision="abcdef123456",
        )

    def test_matches_revision_ignores_plus_suffix(self):
        self.assertTrue(
            PackageRelease.matches_revision("1.0.0+", "abcdef123456")
        )

    def test_is_current_false_when_version_differs(self):
        self.release.version = "2.0.0"
        self.release.save()
        self.assertFalse(self.release.is_current)


class PackageReleaseChangelistTests(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(User.objects.get(username="admin"))

    def test_prepare_next_release_button_present(self):
        response = self.client.get(reverse("admin:core_packagerelease_changelist"))
        prepare_url = reverse(
            "admin:core_packagerelease_actions", args=["prepare_next_release"]
        )
        self.assertContains(response, prepare_url, html=False)

    def test_refresh_from_pypi_button_present(self):
        response = self.client.get(reverse("admin:core_packagerelease_changelist"))
        refresh_url = reverse(
            "admin:core_packagerelease_actions", args=["refresh_from_pypi"]
        )
        self.assertContains(response, refresh_url, html=False)

    def test_prepare_next_release_action_creates_release(self):
        package = Package.objects.get(name="arthexis")
        PackageRelease.all_objects.filter(package=package).delete()
        response = self.client.post(
            reverse(
                "admin:core_packagerelease_actions", args=["prepare_next_release"]
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            PackageRelease.all_objects.filter(package=package).exists()
        )


class PackageAdminPrepareNextReleaseTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = PackageAdmin(Package, self.site)
        self.admin.message_user = lambda *args, **kwargs: None
        self.package = Package.objects.get(name="arthexis")

    def test_prepare_next_release_active_creates_release(self):
        PackageRelease.all_objects.filter(package=self.package).delete()
        request = self.factory.post("/admin/core/package/prepare-next-release/")
        response = self.admin.prepare_next_release_active(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            PackageRelease.all_objects.filter(package=self.package).count(), 1
        )

    def test_prepare_next_release_active_get_creates_release(self):
        PackageRelease.all_objects.filter(package=self.package).delete()
        request = self.factory.get("/admin/core/package/prepare-next-release/")
        response = self.admin.prepare_next_release_active(request)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            PackageRelease.all_objects.filter(package=self.package).exists()
        )


class PackageAdminChangeViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(User.objects.get(username="admin"))
        self.package = Package.objects.get(name="arthexis")

    def test_prepare_next_release_button_visible_on_change_view(self):
        response = self.client.get(
            reverse("admin:core_package_change", args=[self.package.pk])
        )
        self.assertContains(response, "Prepare next Release")


class TodoDoneTests(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(User.objects.get(username="admin"))

    @mock.patch("core.models.revision_utils.get_revision", return_value="rev123")
    def test_mark_done_sets_timestamp(self, _get_revision):
        todo = Todo.objects.create(request="Task", is_seed_data=True)
        resp = self.client.post(reverse("todo-done", args=[todo.pk]))
        self.assertRedirects(resp, reverse("admin:index"))
        todo.refresh_from_db()
        self.assertIsNotNone(todo.done_on)
        self.assertFalse(todo.is_deleted)
        self.assertIsNone(todo.done_node)
        version_path = Path(settings.BASE_DIR) / "VERSION"
        expected_version = ""
        if version_path.exists():
            expected_version = version_path.read_text(encoding="utf-8").strip()
        self.assertEqual(todo.done_version, expected_version)
        self.assertEqual(todo.done_revision, "rev123")
        self.assertEqual(todo.done_username, "admin")

    def test_mark_done_updates_seed_fixture(self):
        todo = Todo.objects.create(request="Task", is_seed_data=True)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fixture_dir = base / "core" / "fixtures"
            fixture_dir.mkdir(parents=True)
            fixture_path = fixture_dir / "todo__task.json"
            created_on_value = timezone.now().replace(microsecond=0).isoformat()
            fixture_path.write_text(
                json.dumps(
                    [
                        {
                            "model": "core.todo",
                            "fields": {
                                "request": "Task",
                                "created_on": created_on_value,
                                "url": "",
                                "request_details": "",
                            },
                        }
                    ],
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with override_settings(BASE_DIR=base):
                with mock.patch(
                    "core.models.revision_utils.get_revision", return_value="rev456"
                ):
                    resp = self.client.post(reverse("todo-done", args=[todo.pk]))

            self.assertRedirects(resp, reverse("admin:index"))
            data = json.loads(fixture_path.read_text(encoding="utf-8"))
            self.assertEqual(len(data), 1)
            fields = data[0]["fields"]
            self.assertIn("done_on", fields)
            self.assertTrue(fields["done_on"])
            self.assertFalse(fields.get("is_deleted", False))
            self.assertIn("done_version", fields)
            self.assertEqual(fields.get("done_revision"), "rev456")
            self.assertEqual(fields.get("done_username"), "admin")

    def test_soft_delete_updates_seed_fixture(self):
        todo = Todo.objects.create(request="Task", is_seed_data=True)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fixture_dir = base / "core" / "fixtures"
            fixture_dir.mkdir(parents=True)
            fixture_path = fixture_dir / "todo__task.json"
            created_on_value = timezone.now().replace(microsecond=0).isoformat()
            fixture_path.write_text(
                json.dumps(
                    [
                        {
                            "model": "core.todo",
                            "fields": {
                                "request": "Task",
                                "created_on": created_on_value,
                                "url": "",
                                "request_details": "",
                            },
                        }
                    ],
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with override_settings(BASE_DIR=base):
                todo.delete()

            data = json.loads(fixture_path.read_text(encoding="utf-8"))
            self.assertEqual(len(data), 1)
            fields = data[0]["fields"]
            self.assertTrue(fields.get("is_deleted"))

    def test_mark_done_missing_task_refreshes(self):
        todo = Todo.objects.create(request="Task", is_seed_data=True)
        todo.delete()
        resp = self.client.post(reverse("todo-done", args=[todo.pk]))
        self.assertRedirects(resp, reverse("admin:index"))
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertFalse(messages)

    def test_mark_done_condition_failure_shows_message(self):
        todo = Todo.objects.create(
            request="Task",
            on_done_condition="1 = 0",
        )
        resp = self.client.post(reverse("todo-done", args=[todo.pk]))
        self.assertRedirects(resp, reverse("admin:index"))
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(messages)
        self.assertIn("1 = 0", messages[0])
        todo.refresh_from_db()
        self.assertIsNone(todo.done_on)

    def test_mark_done_condition_invalid_expression(self):
        todo = Todo.objects.create(
            request="Task",
            on_done_condition="1; SELECT 1",
        )
        resp = self.client.post(reverse("todo-done", args=[todo.pk]))
        self.assertRedirects(resp, reverse("admin:index"))
        messages = [m.message for m in get_messages(resp.wsgi_request)]
        self.assertTrue(messages)
        self.assertIn("Semicolons", messages[0])
        todo.refresh_from_db()
        self.assertIsNone(todo.done_on)

    def test_mark_done_condition_resolves_sigils(self):
        todo = Todo.objects.create(
            request="Task",
            on_done_condition="[TEST]",
        )
        with mock.patch.object(Todo, "resolve_sigils", return_value="1 = 1") as resolver:
            resp = self.client.post(reverse("todo-done", args=[todo.pk]))
        self.assertRedirects(resp, reverse("admin:index"))
        resolver.assert_called_once_with("on_done_condition")
        todo.refresh_from_db()
        self.assertIsNotNone(todo.done_on)

    def test_mark_done_respects_next_parameter(self):
        todo = Todo.objects.create(request="Task")
        next_url = reverse("admin:index") + "?section=todos"
        resp = self.client.post(
            reverse("todo-done", args=[todo.pk]),
            {"next": next_url},
        )
        self.assertRedirects(resp, next_url, target_status_code=200)
        todo.refresh_from_db()
        self.assertIsNotNone(todo.done_on)

    def test_mark_done_rejects_external_next(self):
        todo = Todo.objects.create(request="Task")
        resp = self.client.post(
            reverse("todo-done", args=[todo.pk]),
            {"next": "https://example.com/"},
        )
        self.assertRedirects(resp, reverse("admin:index"))
        todo.refresh_from_db()
        self.assertIsNotNone(todo.done_on)

    def test_env_refresh_preserves_completed_fixture_todo(self):
        base_dir = Path(settings.BASE_DIR)
        fixture_path = base_dir / "core" / "fixtures" / "todo__validate_screen_system_reports.json"
        spec = importlib.util.spec_from_file_location(
            "env_refresh_todo", base_dir / "env-refresh.py"
        )
        env_refresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(env_refresh)
        fixture_rel_path = str(fixture_path.relative_to(base_dir))
        env_refresh._fixture_files = lambda: [fixture_rel_path]

        from django.core.management import call_command as django_call

        def fake_call_command(name, *args, **kwargs):
            if name == "loaddata":
                return django_call(name, *args, **kwargs)
            return None

        env_refresh.call_command = fake_call_command
        env_refresh.load_shared_user_fixtures = lambda *args, **kwargs: None
        env_refresh.load_user_fixtures = lambda *args, **kwargs: None
        env_refresh.generate_model_sigils = lambda: None

        env_refresh.run_database_tasks()

        todo = Todo.objects.get(request="Validate screen System Reports")
        self.assertTrue(todo.is_seed_data)
        todo.done_on = timezone.now()
        todo.save(update_fields=["done_on"])

        env_refresh.run_database_tasks()

        todo.refresh_from_db()
        self.assertIsNotNone(todo.done_on)
        self.assertTrue(todo.is_seed_data)


class TodoDeleteTests(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(User.objects.get(username="admin"))

    def test_delete_marks_task_deleted(self):
        todo = Todo.objects.create(request="Task", is_seed_data=True)
        resp = self.client.post(reverse("todo-delete", args=[todo.pk]))
        self.assertRedirects(resp, reverse("admin:index"))
        todo.refresh_from_db()
        self.assertTrue(todo.is_deleted)
        self.assertIsNone(todo.done_on)

    def test_delete_updates_seed_fixture(self):
        todo = Todo.objects.create(request="Task", is_seed_data=True)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fixture_dir = base / "core" / "fixtures"
            fixture_dir.mkdir(parents=True)
            fixture_path = fixture_dir / "todo__task.json"
            created_on_value = timezone.now().replace(microsecond=0).isoformat()
            fixture_path.write_text(
                json.dumps(
                    [
                        {
                            "model": "core.todo",
                            "fields": {
                                "request": "Task",
                                "created_on": created_on_value,
                                "url": "",
                                "request_details": "",
                            },
                        }
                    ],
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with override_settings(BASE_DIR=base):
                resp = self.client.post(reverse("todo-delete", args=[todo.pk]))
                self.assertRedirects(resp, reverse("admin:index"))
                data = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 1)
        fields = data[0]["fields"]
        self.assertTrue(fields.get("is_deleted"))

    def test_delete_missing_task_redirects(self):
        todo = Todo.objects.create(request="Task")
        todo.is_deleted = True
        todo.save(update_fields=["is_deleted"])
        resp = self.client.post(reverse("todo-delete", args=[todo.pk]))
        self.assertRedirects(resp, reverse("admin:index"))


class TodoFocusViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(User.objects.get(username="admin"))

    def test_focus_view_renders_requested_page(self):
        todo = Todo.objects.create(request="Task", url="/docs/")
        next_url = reverse("admin:index")
        resp = self.client.get(
            f"{reverse('todo-focus', args=[todo.pk])}?next={quote(next_url)}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, todo.request)
        self.assertEqual(resp["X-Frame-Options"], "SAMEORIGIN")
        self.assertContains(resp, f'src="{todo.url}"')
        self.assertContains(resp, "Done")
        self.assertContains(resp, "Delete")
        self.assertContains(resp, "Back")
        self.assertContains(resp, "Take Snapshot")
        snapshot_url = reverse("todo-snapshot", args=[todo.pk])
        self.assertContains(resp, snapshot_url)

    def test_focus_view_uses_admin_change_when_no_url(self):
        todo = Todo.objects.create(request="Task")
        resp = self.client.get(reverse("todo-focus", args=[todo.pk]))
        change_url = reverse("admin:core_todo_change", args=[todo.pk])
        self.assertContains(resp, f'src="{change_url}"')

    def test_focus_view_includes_open_target_button(self):
        todo = Todo.objects.create(request="Task", url="/docs/")
        resp = self.client.get(reverse("todo-focus", args=[todo.pk]))
        self.assertContains(resp, 'class="todo-button todo-button-open"')
        self.assertContains(resp, 'target="_blank"')
        self.assertContains(resp, 'href="/docs/"')

    def test_focus_view_sanitizes_loopback_absolute_url(self):
        todo = Todo.objects.create(
            request="Task",
            url="http://127.0.0.1:8888/docs/?section=chart",
        )
        resp = self.client.get(reverse("todo-focus", args=[todo.pk]))
        self.assertContains(resp, 'src="/docs/?section=chart"')

    def test_focus_view_rejects_external_absolute_url(self):
        todo = Todo.objects.create(
            request="Task",
            url="https://outside.invalid/external/",
        )
        resp = self.client.get(reverse("todo-focus", args=[todo.pk]))
        change_url = reverse("admin:core_todo_change", args=[todo.pk])
        self.assertContains(resp, f'src="{change_url}"')

    def test_focus_view_avoids_recursive_focus_url(self):
        todo = Todo.objects.create(request="Task")
        focus_url = reverse("todo-focus", args=[todo.pk])
        Todo.objects.filter(pk=todo.pk).update(url=focus_url)
        resp = self.client.get(reverse("todo-focus", args=[todo.pk]))
        change_url = reverse("admin:core_todo_change", args=[todo.pk])
        self.assertContains(resp, f'src="{change_url}"')

    def test_focus_view_avoids_recursive_focus_absolute_url(self):
        todo = Todo.objects.create(request="Task")
        focus_url = reverse("todo-focus", args=[todo.pk])
        Todo.objects.filter(pk=todo.pk).update(url=f"http://testserver{focus_url}")
        resp = self.client.get(reverse("todo-focus", args=[todo.pk]))
        change_url = reverse("admin:core_todo_change", args=[todo.pk])
        self.assertContains(resp, f'src="{change_url}"')

    def test_focus_view_parses_auth_directives(self):
        todo = Todo.objects.create(
            request="Task",
            url="/docs/?section=chart&_todo_auth=logout&_todo_auth=user:demo&_todo_auth=perm:core.view_user&_todo_auth=extra",
        )
        resp = self.client.get(reverse("todo-focus", args=[todo.pk]))
        self.assertContains(resp, 'src="/docs/?section=chart"')
        self.assertContains(resp, 'href="/docs/?section=chart"')
        self.assertContains(resp, "logged out")
        self.assertContains(resp, "Sign in using: demo")
        self.assertContains(resp, "Required permissions: core.view_user")
        self.assertContains(resp, "Additional authentication notes: extra")

    def test_focus_view_redirects_if_todo_completed(self):
        todo = Todo.objects.create(request="Task")
        todo.done_on = timezone.now()
        todo.save(update_fields=["done_on"])
        next_url = reverse("admin:index")
        resp = self.client.get(
            f"{reverse('todo-focus', args=[todo.pk])}?next={quote(next_url)}"
        )
        self.assertRedirects(resp, next_url, target_status_code=200)


class TodoSnapshotViewTests(TestCase):
    PNG_PIXEL = (
        "data:image/png;base64," "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR42mP8/5+hHgAFgwJ/lSdX6QAAAABJRU5ErkJggg=="
    )

    def setUp(self):
        self.user = User.objects.create_user(
            username="manager", password="secret", is_staff=True
        )
        self.client.force_login(self.user)
        self.todo = Todo.objects.create(
            request="QA release notes", request_details="Verify layout"
        )

    def test_snapshot_creates_content_sample(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with override_settings(LOG_DIR=Path(tmpdir.name)):
            response = self.client.post(
                reverse("todo-snapshot", args=[self.todo.pk]),
                data=json.dumps({"image": self.PNG_PIXEL}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("sample", payload)
        self.assertEqual(ContentSample.objects.filter(kind=ContentSample.IMAGE).count(), 1)
        sample = ContentSample.objects.get(pk=payload["sample"])
        self.assertEqual(sample.method, "TODO_QA")
        self.assertEqual(sample.user, self.user)
        self.assertEqual(sample.content, "QA release notes — Verify layout")
        rel_path = Path(sample.path)
        self.assertTrue(rel_path.parts)
        self.assertEqual(rel_path.parts[0], "screenshots")
        self.assertTrue((Path(tmpdir.name) / rel_path).exists())

    def test_snapshot_rejects_completed_todo(self):
        self.todo.done_on = timezone.now()
        self.todo.save(update_fields=["done_on"])
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        with override_settings(LOG_DIR=Path(tmpdir.name)):
            response = self.client.post(
                reverse("todo-snapshot", args=[self.todo.pk]),
                data=json.dumps({"image": self.PNG_PIXEL}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ContentSample.objects.count(), 0)


class TodoUrlValidationTests(TestCase):
    def test_relative_url_valid(self):
        todo = Todo(request="Task", url="/path")
        todo.full_clean()  # should not raise

    def test_absolute_url_invalid(self):
        todo = Todo(request="Task", url="https://example.com/path")
        with self.assertRaises(ValidationError):
            todo.full_clean()


class TodoUniqueTests(TestCase):
    def test_request_unique_case_insensitive(self):
        Todo.objects.create(request="Task")
        with self.assertRaises(IntegrityError):
            Todo.objects.create(request="task")


class TodoCreatedOnMigrationTests(TransactionTestCase):
    reset_sequences = True

    migrate_from = ("core", "0091_todo_version")
    migrate_to = ("core", "0095_todo_created_on_backfill")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        self.apps = self.executor.loader.project_state(self.migrate_from).apps

    def tearDown(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_migration_adds_created_on_column(self):
        TodoModel = self.apps.get_model("core", "Todo")
        todo = TodoModel.objects.create(
            request="Verify release publish TODO timeline timestamps"
        )
        todo_pk = todo.pk
        table = TodoModel._meta.db_table
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, table
                )
            }
        self.assertNotIn("created_on", columns)

        self.executor.migrate([self.migrate_to])
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        TodoNew = new_apps.get_model("core", "Todo")
        table_new = TodoNew._meta.db_table
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, table_new
                )
            }
        self.assertIn("created_on", columns)
        manager = getattr(TodoNew, "all_objects", TodoNew._base_manager)
        refreshed = manager.get(pk=todo_pk)
        self.assertIsNotNone(refreshed.created_on)


class TodoAdminPermissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(User.objects.get(username="admin"))

    def test_add_view_disallowed(self):
        resp = self.client.get(reverse("admin:core_todo_add"))
        self.assertEqual(resp.status_code, 403)

    def test_change_form_loads(self):
        todo = Todo.objects.create(request="Task")
        resp = self.client.get(reverse("admin:core_todo_change", args=[todo.pk]))
        self.assertEqual(resp.status_code, 200)
