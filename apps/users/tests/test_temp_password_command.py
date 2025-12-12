import io

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

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

    def test_staff_superuser_flags_require_create(self):
        identifier = "existing@example.com"
        User = get_user_model()
        User.objects.create_user(username=identifier, email=identifier)

        with self.assertRaisesMessage(
            CommandError,
            "--staff and --superuser can only be used with --create or --update.",
        ):
            call_command("temp_password", identifier, staff=True)

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
