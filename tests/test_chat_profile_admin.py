"""Tests for ChatProfile admin key generation."""

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import TestCase

from core.models import ChatProfile, hash_key


class ChatProfileAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="admin", email="a@example.com", password="pwd"
        )
        self.user = User.objects.create_user(username="bob", password="pwd")
        self.profile = ChatProfile.objects.create(
            user=self.user, user_key_hash="0" * 64
        )
        self.client.force_login(self.admin)

    def test_change_form_has_generate_key_link(self):
        url = reverse(
            "admin:core_chatprofile_change",
            args=[self.profile.pk],
        )
        response = self.client.get(url)
        self.assertContains(response, "../generate-key/")

    def test_change_form_shows_gpt_instructions(self):
        url = reverse(
            "admin:core_chatprofile_change",
            args=[self.profile.pk],
        )
        response = self.client.get(url)
        self.assertContains(response, "/api/chat/")
        self.assertContains(response, "Authorization")
        self.assertContains(response, "GPT")

    def test_generate_key_button(self):
        url = reverse(
            "admin:core_chatprofile_generate_key",
            args=[self.profile.pk],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        key = response.context["user_key"]
        self.assertTrue(key)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.user_key_hash, hash_key(key))
