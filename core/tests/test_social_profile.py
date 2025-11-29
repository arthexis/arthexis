from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase

from teams.models import SocialProfile
from core.models import User


class SocialProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="testpass123")

    def test_normalizes_handle_and_domain(self):
        profile = SocialProfile(
            user=self.user,
            handle="Example.COM",
            domain="Example.COM",
            did="did:plc:abcdef1234567890",
        )
        profile.full_clean()
        profile.save()

        self.assertEqual(profile.handle, "example.com")
        self.assertEqual(profile.domain, "example.com")
        self.assertEqual(profile.network, SocialProfile.Network.BLUESKY)

    def test_invalid_domain_raises_validation_error(self):
        profile = SocialProfile(
            user=self.user,
            handle="invalid domain",
            domain="invalid domain",
        )
        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_invalid_did_raises_validation_error(self):
        profile = SocialProfile(
            user=self.user,
            handle="example.com",
            domain="example.com",
            did="not-a-did",
        )
        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_did_is_optional(self):
        profile = SocialProfile(
            user=self.user,
            handle="example.com",
            domain="example.com",
        )
        profile.full_clean()
        profile.save()

        self.assertEqual(profile.did, "")

    def test_unique_handle_per_network(self):
        SocialProfile.objects.create(
            user=self.user,
            handle="example.com",
            domain="example.com",
            did="did:plc:abcdef1234567890",
        )
        second_user = User.objects.create_user(username="other", password="pass12345")
        duplicate = SocialProfile(
            user=second_user,
            handle="example.com",
            domain="example.com",
            did="did:plc:9876543210abcdef",
        )

        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_str_returns_handle_and_network(self):
        profile = SocialProfile(
            user=self.user,
            handle="example.com",
            domain="example.com",
        )
        profile.full_clean()
        profile.save()

        self.assertEqual(str(profile), "example.com@bluesky")

    def test_str_resolves_sigils(self):
        profile = SocialProfile(
            user=self.user,
            handle="example.com",
            domain="example.com",
        )
        profile.full_clean()
        profile.save()

        with mock.patch.object(
            profile,
            "resolve_sigils",
            side_effect=["resolved", "sigil-net"],
        ) as resolver:
            self.assertEqual(str(profile), "resolved@sigil-net")
            resolver.assert_has_calls(
                [mock.call("handle"), mock.call("network")]
            )
