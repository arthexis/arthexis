import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.users.backends import TempPasswordBackend
from apps.users import temp_passwords


class TempPasswordCommandTests(TestCase):
    def test_error_when_user_missing_without_create_flag(self):
        identifier = "missing@example.com"

        with self.assertRaisesMessage(
            CommandError, f"No user found for identifier '{identifier}'."
        ):
            call_command("temp_password", identifier)

    def test_creates_user_when_create_flag_provided(self):
        identifier = "new-user@example.com"

        stdout = io.StringIO()
        call_command("temp_password", identifier, create=True, stdout=stdout)

        User = get_user_model()
        user = User.all_objects.get(username=identifier)
        assert user.email == identifier
        assert not user.has_usable_password()

        entry = temp_passwords.load_temp_password(identifier)
        assert entry is not None
        assert not entry.is_expired

        output = stdout.getvalue()
        assert f"Temporary password for {identifier}:" in output
        assert "Temporary password created." in output

    def test_create_user_with_staff_and_superuser_flags(self):
        identifier = "privileged@example.com"

        call_command(
            "temp_password",
            identifier,
            create=True,
            staff=True,
            superuser=True,
        )

        User = get_user_model()
        user = User.all_objects.get(username=identifier)
        assert user.is_staff
        assert user.is_superuser

    def test_create_flag_updates_existing_user_permissions(self):
        identifier = "existing-privileged@example.com"
        User = get_user_model()
        user = User.objects.create_user(username=identifier, email=identifier)

        call_command(
            "temp_password",
            identifier,
            create=True,
            staff=True,
            superuser=True,
        )

        user.refresh_from_db()
        assert user.is_staff
        assert user.is_superuser

    def test_staff_superuser_flags_require_create(self):
        identifier = "existing@example.com"
        User = get_user_model()
        User.objects.create_user(username=identifier, email=identifier)

        with self.assertRaisesMessage(
            CommandError,
            "--staff and --superuser can only be used with --create or --update.",
        ):
            call_command("temp_password", identifier, staff=True)

    def test_staff_and_superuser_flags_require_create_or_update(self):
        identifier = "existing-staff@example.com"
        User = get_user_model()
        User.objects.create_user(username=identifier, email=identifier)

        with self.assertRaisesMessage(
            CommandError,
            "--staff and --superuser can only be used with --create or --update.",
        ):
            call_command("temp_password", identifier, staff=True, superuser=True)

    def test_update_user_permissions(self):
        identifier = "existing-staff@example.com"
        User = get_user_model()
        user = User.objects.create_user(username=identifier, email=identifier)

        call_command(
            "temp_password",
            identifier,
            update=True,
            staff=True,
            superuser=True,
        )

        user.refresh_from_db()
        assert user.is_staff
        assert user.is_superuser

    def test_existing_user_not_elevated_without_update(self):
        identifier = "existing-unchanged@example.com"
        User = get_user_model()
        user = User.objects.create_user(username=identifier, email=identifier)

        call_command("temp_password", identifier)

        user.refresh_from_db()
        assert not user.is_staff
        assert not user.is_superuser

    def test_create_arthexis_user_by_username(self):
        identifier = "arthexis"
        User = get_user_model()
        User.all_objects.filter(username=identifier).delete()

        stdout = io.StringIO()
        call_command(
            "temp_password",
            identifier,
            create=True,
            staff=True,
            superuser=True,
            stdout=stdout,
        )

        user = User.all_objects.get(username=identifier)
        assert user.email in {"", None}
        assert user.is_staff
        assert user.is_superuser

        entry = temp_passwords.load_temp_password(identifier)
        assert entry is not None
        assert not entry.is_expired

        output = stdout.getvalue()
        assert f"Temporary password for {identifier}:" in output
        assert "Temporary password created." in output

    def test_update_arthexis_user(self):
        identifier = "arthexis"
        User = get_user_model()
        User.all_objects.filter(username=identifier).delete()
        user = User.all_objects.create_user(username=identifier, email="admin@example.com")

        call_command(
            "temp_password",
            identifier,
            update=True,
            staff=True,
            superuser=True,
        )

        user.refresh_from_db()
        assert user.is_staff
        assert user.is_superuser

        entry = temp_passwords.load_temp_password(identifier)
        assert entry is not None
        assert not entry.is_expired

    def test_new_temp_password_clears_expired_temporary_lock(self):
        identifier = "expired@example.com"
        User = get_user_model()
        user = User.all_objects.create_user(username=identifier, email=identifier)
        user.temporary_expires_at = timezone.now() - timedelta(hours=1)
        user.is_active = False
        user.save(update_fields=["temporary_expires_at", "is_active"])

        with patch("apps.users.temp_passwords.generate_password", return_value="TempPass123"):
            call_command("temp_password", identifier, update=True)

        user.refresh_from_db()
        assert user.temporary_expires_at is None

        backend = TempPasswordBackend()
        authed = backend.authenticate(None, username=identifier, password="TempPass123")
        assert authed is not None
        authed.refresh_from_db()
        assert authed.is_active

    def test_expires_in_must_be_positive(self):
        identifier = "expires@example.com"
        User = get_user_model()
        User.objects.create_user(username=identifier, email=identifier)

        with self.subTest("zero"):
            with self.assertRaisesMessage(
                CommandError, "Expiration must be a positive number of seconds."
            ):
                call_command("temp_password", identifier, update=True, expires_in=0)

        with self.subTest("negative"):
            with self.assertRaisesMessage(
                CommandError, "Expiration must be a positive number of seconds."
            ):
                call_command("temp_password", identifier, update=True, expires_in=-1)

    def test_multiple_users_with_same_email_reported(self):
        User = get_user_model()
        email = "shared@example.com"
        User.objects.create_user(username="user-a", email=email)
        User.objects.create_user(username="user-b", email=email)

        with self.assertRaisesMessage(
            CommandError,
            "Multiple users share this email address. Provide the username instead. Matches: user-a, user-b",
        ):
            call_command("temp_password", email)

    def test_expiration_cleared_when_updating_permissions(self):
        identifier = "reactivate@example.com"
        User = get_user_model()
        user = User.objects.create_user(username=identifier, email=identifier)
        user.temporary_expires_at = timezone.now() - timedelta(minutes=5)
        user.save(update_fields=["temporary_expires_at"])

        call_command("temp_password", identifier, update=True, staff=True)

        user.refresh_from_db()
        assert user.temporary_expires_at is None
        assert user.is_staff
