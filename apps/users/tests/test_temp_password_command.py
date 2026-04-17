import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.groups.constants import AP_USER_GROUP_NAME
from apps.groups.constants import EXTERNAL_AGENT_GROUP_NAME
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

    def test_access_point_user_requires_identifier(self):
        """Access-point mode should reject invocations without an identifier."""

        with self.assertRaisesMessage(
            CommandError,
            "identifier is required when using --access-point-user.",
        ):
            call_command("password", access_point_user=True)

    def test_assigns_group_with_group_option(self):
        """A user should be assignable to existing groups from the password command."""

        user = get_user_model().objects.create_user(username="group-user", email="group@example.com")
        Group.objects.create(name="operators")

        call_command("password", user.username, password="valid-pass-123", group="operators")

        user.refresh_from_db()
        assert user.groups.filter(name="operators").exists()


    def test_create_staff_user_defaults_to_external_agent(self):
        """Creating a staff user without explicit groups should add External Agent."""

        call_command(
            "password",
            "default-staff",
            create=True,
            staff=True,
            password="valid-pass-123",
        )

        user = get_user_model().objects.get(username="default-staff")
        assert user.groups.filter(name=EXTERNAL_AGENT_GROUP_NAME).exists()

    def test_create_staff_user_with_explicit_group_skips_external_agent_default(self):
        """Explicit group assignment should suppress the generic staff default."""

        Group.objects.create(name="operators")

        call_command(
            "password",
            "explicit-staff",
            create=True,
            staff=True,
            password="valid-pass-123",
            group="operators",
        )

        user = get_user_model().objects.get(username="explicit-staff")
        assert user.groups.filter(name="operators").exists()
        assert not user.groups.filter(name=EXTERNAL_AGENT_GROUP_NAME).exists()

    def test_group_option_requires_existing_group(self):
        """A clear error should be raised when --group references an unknown group."""

        user = get_user_model().objects.create_user(username="missing-group", email="missing@example.com")

        with self.assertRaisesMessage(CommandError, "Unknown groups: missing"):
            call_command("password", user.username, password="valid-pass-123", group="missing")

    def test_configures_access_point_user_mode(self):
        """Access-point mode should disable passwords and keep the user non-staff."""

        Group.objects.create(name=AP_USER_GROUP_NAME)
        user = get_user_model().objects.create_user(
            username="ap-user",
            email="ap-user@example.com",
            password="InitialPassword123",
            is_staff=True,
            is_superuser=True,
            force_password_change=True,
        )

        call_command(
            "password",
            user.username,
            update=True,
            access_point_user=True,
            group=AP_USER_GROUP_NAME,
        )

        user.refresh_from_db()
        assert not user.is_staff
        assert not user.is_superuser
        assert not user.has_usable_password()
        assert user.allow_local_network_passwordless_login is True
        assert user.force_password_change is False
        assert user.groups.filter(name=AP_USER_GROUP_NAME).exists()

    def test_configures_access_point_user_mode_without_update_flag(self):
        """Access-point mode should demote privileges even without --update."""

        Group.objects.create(name=AP_USER_GROUP_NAME)
        user = get_user_model().objects.create_user(
            username="ap-no-update",
            email="ap-no-update@example.com",
            password="InitialPassword123",
            is_staff=True,
            is_superuser=True,
            force_password_change=True,
        )

        call_command(
            "password",
            user.username,
            access_point_user=True,
            group=AP_USER_GROUP_NAME,
        )

        user.refresh_from_db()
        assert not user.is_staff
        assert not user.is_superuser
        assert not user.has_usable_password()
        assert user.allow_local_network_passwordless_login is True
        assert user.force_password_change is False
        assert user.groups.filter(name=AP_USER_GROUP_NAME).exists()

    def test_configures_access_point_user_mode_clears_existing_groups(self):
        """Access-point mode should drop prior group access before reassignment."""

        Group.objects.create(name=AP_USER_GROUP_NAME)
        legacy_group = Group.objects.create(name="Legacy Admin")
        user = get_user_model().objects.create_user(
            username="ap-groups-clear",
            email="ap-groups-clear@example.com",
            password="InitialPassword123",
            is_staff=True,
            is_superuser=True,
        )
        user.groups.add(legacy_group)

        call_command(
            "password",
            user.username,
            access_point_user=True,
            group=AP_USER_GROUP_NAME,
        )

        user.refresh_from_db()
        assert not user.groups.filter(name=legacy_group.name).exists()
        assert user.groups.filter(name=AP_USER_GROUP_NAME).exists()

    def test_access_point_user_mode_preserves_groups_when_requested_group_is_unknown(self):
        """Failed group reassignment should not clear existing memberships."""

        legacy_group = Group.objects.create(name="Legacy Admin")
        user = get_user_model().objects.create_user(
            username="ap-groups-unknown",
            email="ap-groups-unknown@example.com",
            password="InitialPassword123",
        )
        user.groups.add(legacy_group)

        with self.assertRaisesMessage(CommandError, "Unknown groups: missing-ap"):
            call_command(
                "password",
                user.username,
                access_point_user=True,
                group="missing-ap",
            )

        user.refresh_from_db()
        assert user.groups.filter(name=legacy_group.name).exists()

    def test_configures_access_point_user_mode_clears_temporary_credentials(self):
        """Access-point mode should clear temporary-password state for hardened login."""

        Group.objects.create(name=AP_USER_GROUP_NAME)
        user = get_user_model().objects.create_user(
            username="ap-temp-clear",
            email="ap-temp-clear@example.com",
            password="InitialPassword123",
            temporary_expires_at=timezone.now() + timedelta(hours=1),
        )
        temp_passwords.store_temp_password(user.username, "TemporaryPassword123")

        call_command(
            "password",
            user.username,
            access_point_user=True,
            group=AP_USER_GROUP_NAME,
        )

        user.refresh_from_db()
        assert user.temporary_expires_at is None
        assert temp_passwords.load_temp_password(user.username) is None

    def test_access_point_user_mode_rejects_password_argument(self):
        """Access-point mode should reject contradictory password arguments."""

        user = get_user_model().objects.create_user(
            username="ap-invalid",
            email="ap-invalid@example.com",
        )

        with self.assertRaisesMessage(
            CommandError,
            "--access-point-user cannot be combined with --password.",
        ):
            call_command(
                "password",
                user.username,
                update=True,
                access_point_user=True,
                password="AnyPassword123",
            )
