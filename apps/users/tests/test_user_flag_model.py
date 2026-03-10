"""Tests for user-level flag storage."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from apps.users.models import UserFlag


class UserFlagModelTests(TestCase):
    """Validate uniqueness and serialization for user flags."""

    def test_user_flag_key_is_unique_per_user(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="flags-user", password="test-pass")
        UserFlag.objects.create(user=user, key="tutorial_complete", is_enabled=True)

        with self.assertRaises(IntegrityError):
            UserFlag.objects.create(user=user, key="tutorial_complete", is_enabled=False)

    def test_user_flag_can_store_json_value(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="flags-user-json", password="test-pass")

        flag = UserFlag.objects.create(
            user=user,
            key="experience_profile",
            value={"difficulty": "normal", "xp": 320},
        )

        self.assertEqual(flag.value["xp"], 320)
