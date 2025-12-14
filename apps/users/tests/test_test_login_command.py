import io

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.users import temp_passwords


class TestLoginCommandTests(TestCase):
    def test_authenticates_with_temp_password(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="temp-command", email="temp-command@example.com"
        )
        user.set_unusable_password()
        user.save()
        password = "TempPass123"
        temp_passwords.store_temp_password(user.username, password)

        stdout = io.StringIO()
        call_command(
            "test_login",
            user.email,
            password=password,
            stdout=stdout,
        )

        output = stdout.getvalue()
        assert "Authentication succeeded" in output
        assert user.username in output

    def test_admin_defaults_allow_admin_login(self):
        stdout = io.StringIO()
        call_command(
            "test_login",
            "admin",
            password="admin",
            stdout=stdout,
        )

        output = stdout.getvalue()
        assert "Authentication succeeded" in output
        assert "admin" in output
