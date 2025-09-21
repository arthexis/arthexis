"""Tests for assistant profile issuance and authentication."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.test import TestCase

from core.models import AssistantProfile, hash_key


class AssistantProfileAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="alice", password="pwd")

    def test_issue_and_authenticate(self):
        issue_url = reverse("workgroup:assistantprofile-issue", args=[self.user.id])
        response = self.client.post(issue_url)
        self.assertEqual(response.status_code, 200)
        key = response.json()["user_key"]

        profile = AssistantProfile.objects.get(user=self.user)
        self.assertEqual(profile.user_key_hash, hash_key(key))

        test_url = reverse("workgroup:assistant-test")
        ok = self.client.get(test_url, HTTP_AUTHORIZATION=f"Bearer {key}")
        self.assertEqual(ok.status_code, 200)
        self.assertIn(str(self.user.id), ok.json()["message"])

        bad = self.client.get(test_url, HTTP_AUTHORIZATION="Bearer bad")
        self.assertEqual(bad.status_code, 401)

    def test_system_user_can_have_profile(self):
        User = get_user_model()
        system_user, _ = User.objects.get_or_create(
            username=User.SYSTEM_USERNAME,
            defaults={"email": "arthexis@example.com"},
        )
        AssistantProfile.objects.filter(user=system_user).delete()

        profile = AssistantProfile(
            user=system_user,
            user_key_hash=hash_key("system-profile-allowed"),
        )

        try:
            profile.full_clean()
        except ValidationError as exc:  # pragma: no cover - explicit failure path
            self.fail(f"System user should allow profiles: {exc}")
