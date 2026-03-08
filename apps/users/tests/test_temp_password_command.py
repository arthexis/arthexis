import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.users import temp_passwords
from apps.users.backends import TempPasswordBackend


class PasswordCommandTests(TestCase):
    """Coverage for the password management command."""

    def test_generates_password_without_identifier(self):
        """The command should allow pure password generation with no user target."""

        stdout = io.StringIO()
        call_command("password", stdout=stdout)

        output = stdout.getvalue()
        assert "Generated password:" in output
        assert "Password generated." in output

    def test_sets_permanent_password_and_forces_change_by_default(self):
        """Permanent password operations should default to force password change."""

        user = get_user_model().objects.create_user(username="perm-user", email="perm@example.com")

        call_command("password", "perm-user", password="permanent-password-for-tests")

        user.refresh_from_db()
        assert user.check_password("permanent-password-for-tests")
        assert user.force_password_change is True

    def test_temporary_password_can_be_targeted_by_user_id_lookup(self):
        """Users should be resolvable by id when generating temporary passwords."""

        user = get_user_model().objects.create_user(username="id-user", email="id@example.com")

        call_command("password", str(user.pk), lookup="id", temporary=True)

        entry = temp_passwords.load_temp_password(user.username)
        assert entry is not None
        assert not entry.is_expired

    def test_temporary_password_clears_expired_temporary_lock(self):
        """Generating a new temporary password should reactivate an expired temporary user."""

        identifier = "expired@example.com"
        user = get_user_model().all_objects.create_user(username=identifier, email=identifier)
        user.temporary_expires_at = timezone.now() - timedelta(hours=1)
        user.is_active = False
        user.save(update_fields=["temporary_expires_at", "is_active"])

        with patch("apps.users.temp_passwords.generate_password", return_value="TempPass123"):
            call_command("password", identifier, temporary=True, update=True)

        user.refresh_from_db()
        assert user.temporary_expires_at is None

        backend = TempPasswordBackend()
        authed = backend.authenticate(None, username=identifier, password="TempPass123")
        assert authed is not None
        authed.refresh_from_db()
        assert authed.is_active

    def test_delete_password_disables_password_and_force_flag(self):
        """Deleting a password should set an unusable password and clear force-change flag."""

        user = get_user_model().objects.create_user(
            username="delete-user",
            email="delete@example.com",
            password="Temp1234",
            force_password_change=True,
        )

        temp_passwords.store_temp_password(user.username, "TempDelete123")

        call_command("password", user.username, delete=True)

        user.refresh_from_db()
        assert not user.has_usable_password()
        assert user.force_password_change is False
        assert temp_passwords.load_temp_password(user.username) is None

    def test_expires_in_must_be_positive_for_temporary_passwords(self):
        """Temporary password expiration must remain a positive duration."""

        identifier = "expires@example.com"
        get_user_model().objects.create_user(username=identifier, email=identifier)

        with self.assertRaisesMessage(
            CommandError, "Expiration must be a positive number of seconds."
        ):
            call_command("password", identifier, temporary=True, expires_in=0)

    def test_missing_user_without_create_raises_error(self):
        """The command should keep explicit messaging when users are missing."""

        identifier = "missing@example.com"
        with self.assertRaisesMessage(
            CommandError,
            f"No user found for identifier '{identifier}'. Use --create to add one.",
        ):
            call_command("password", identifier)

    def test_group_option_requires_identifier(self):
        """Group assignment should fail fast when no target user identifier is provided."""

        Group.objects.create(name="operators")

        with self.assertRaisesMessage(CommandError, "identifier is required when using --group."):
            call_command("password", group="operators")

    def test_assigns_group_with_group_option(self):
        """A user should be assignable to existing groups from the password command."""

        user = get_user_model().objects.create_user(username="group-user", email="group@example.com")
        Group.objects.create(name="operators")

        call_command("password", user.username, password="valid-pass-123", group="operators")

        user.refresh_from_db()
        assert user.groups.filter(name="operators").exists()

    def test_group_option_requires_existing_group(self):
        """A clear error should be raised when --group references an unknown group."""

        user = get_user_model().objects.create_user(username="missing-group", email="missing@example.com")

        with self.assertRaisesMessage(CommandError, "Unknown groups: missing"):
            call_command("password", user.username, password="valid-pass-123", group="missing")
