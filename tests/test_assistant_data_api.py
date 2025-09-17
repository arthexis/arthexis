"""Tests for the generic chat data endpoint."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import TestCase


class AssistantDataEndpointTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="alice", password="pwd")
        issue_url = reverse("workgroup:assistantprofile-issue", args=[self.user.id])
        self.api_key = self.client.post(issue_url).json()["user_key"]

    def test_requires_authentication(self):
        url = reverse("workgroup:chat")
        response = self.client.get(
            url, {"model": settings.AUTH_USER_MODEL, "pk": self.user.id}
        )
        self.assertEqual(response.status_code, 401)

    def test_returns_model_data(self):
        url = reverse("workgroup:chat")
        response = self.client.get(
            url,
            {"model": settings.AUTH_USER_MODEL, "pk": self.user.id},
            HTTP_AUTHORIZATION=f"Bearer {self.api_key}",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["username"], "alice")
