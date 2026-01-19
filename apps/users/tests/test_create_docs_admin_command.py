from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


class CreateDocsAdminCommandTests(TestCase):
    def test_requires_confirm_flag(self):
        with self.assertRaisesMessage(
            CommandError,
            "Pass --confirm to create or update the docs admin user.",
        ):
            call_command("create_docs_admin", password="docs")

    def test_creates_docs_admin_user(self):
        call_command("create_docs_admin", confirm=True, password="docs")

        User = get_user_model()
        user = User.all_objects.get(username="docs")
        assert user.is_staff
        assert user.is_superuser
        assert user.is_active
        assert user.check_password("docs")

    def test_updates_existing_user(self):
        User = get_user_model()
        user = User.objects.create_user(username="docs", email="old@example.com")
        user.is_staff = False
        user.is_superuser = False
        user.is_active = False
        user.save()

        call_command(
            "create_docs_admin",
            confirm=True,
            email="docs@example.com",
            password="docs",
        )

        user.refresh_from_db()
        assert user.email == "docs@example.com"
        assert user.is_staff
        assert user.is_superuser
        assert user.is_active
        assert user.check_password("docs")

    def test_clears_soft_deleted_user(self):
        User = get_user_model()
        user = User.all_objects.create_user(username="docs", email="docs@example.com")
        user.is_deleted = True
        user.save(update_fields=["is_deleted"])

        call_command("create_docs_admin", confirm=True, password="docs")

        user.refresh_from_db()
        assert user.is_deleted is False
        assert user.check_password("docs")
