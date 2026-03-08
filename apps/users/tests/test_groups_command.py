import io

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


class GroupsCommandTests(TestCase):
    """Coverage for group listing and membership management command behavior."""

    def test_lists_groups_with_members(self):
        """The command should print groups, member counts, and usernames."""

        user_model = get_user_model()
        alice = user_model.objects.create_user(username="alice")
        bob = user_model.objects.create_user(username="bob")
        operators = Group.objects.create(name="operators")
        operators.user_set.add(alice, bob)

        stdout = io.StringIO()
        call_command("groups", stdout=stdout)

        output = stdout.getvalue()
        assert "operators (2): alice, bob" in output

    def test_add_and_remove_members(self):
        """The command should allow adding and removing members in one invocation."""

        user_model = get_user_model()
        alice = user_model.objects.create_user(username="alice")
        bob = user_model.objects.create_user(username="bob")
        operators = Group.objects.create(name="operators")
        operators.user_set.add(bob)

        call_command("groups", "operators", add="alice", remove="bob")

        operators.refresh_from_db()
        assert operators.user_set.filter(username="alice").exists()
        assert not operators.user_set.filter(username="bob").exists()

    def test_cannot_add_and_remove_same_user_in_one_call(self):
        """Conflicting membership operations should raise a clear command error."""

        user_model = get_user_model()
        user_model.objects.create_user(username="alice")
        Group.objects.create(name="operators")

        with self.assertRaisesMessage(
            CommandError,
            "Cannot add and remove the same users: alice",
        ):
            call_command("groups", "operators", add="alice", remove="alice")

    def test_membership_changes_require_group_name(self):
        """Missing group name should raise a command error for membership edits."""

        with self.assertRaisesMessage(
            CommandError,
            "A group name is required when using --add/--remove.",
        ):
            call_command("groups", add="alice")

    def test_membership_changes_require_existing_users(self):
        """Unknown usernames should produce a clear command error."""

        Group.objects.create(name="operators")

        with self.assertRaisesMessage(CommandError, "Unknown users: missing-user"):
            call_command("groups", "operators", add="missing-user")
