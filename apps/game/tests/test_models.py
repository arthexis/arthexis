"""Tests for game avatar behavior."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.game.models import Avatar


class AvatarModelTests(TestCase):
    """Validate avatar activation and display behavior."""

    def test_saving_new_active_avatar_deactivates_previous_active_avatar(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="player-one", password="test-pass")
        first_avatar = Avatar.objects.create(user=user, nickname="First", is_active=True)

        second_avatar = Avatar.objects.create(user=user, nickname="Second", is_active=True)

        first_avatar.refresh_from_db()
        self.assertFalse(first_avatar.is_active)
        self.assertTrue(second_avatar.is_active)

    def test_avatar_str_uses_nickname_and_username_when_present(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="player-two", password="test-pass")
        avatar = Avatar.objects.create(user=user, nickname="Hero")

        self.assertEqual(str(avatar), "Hero (player-two)")
