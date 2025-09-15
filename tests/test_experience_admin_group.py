from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.admin.sites import site

from core.admin import ExperienceReference
from core.models import CustomSigil


class ExperienceAdminGroupTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="exp-admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def test_reference_registered(self):
        registry = site._registry
        self.assertIn(ExperienceReference, registry)
        self.assertEqual(registry[ExperienceReference].model._meta.app_label, "pages")

    def test_custom_sigil_registered(self):
        registry = site._registry
        self.assertIn(CustomSigil, registry)
        self.assertEqual(registry[CustomSigil].model._meta.app_label, "pages")

    def test_admin_index_shows_reference(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "7. Experience")
        self.assertContains(response, "References")
        self.assertContains(response, "Custom Sigils")
        self.assertContains(response, "View Histories")
