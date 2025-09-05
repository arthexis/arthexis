from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.admin.sites import site

from core.admin import (
    EmailInbox as PostOfficeEmailInbox,
    WorkgroupReleaseManager,
    WorkgroupSecurityGroup,
)
from nodes.admin import EmailOutbox as PostOfficeEmailOutbox


class WorkgroupAdminGroupTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="po-admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def test_models_registered(self):
        registry = site._registry
        self.assertIn(PostOfficeEmailInbox, registry)
        self.assertIn(PostOfficeEmailOutbox, registry)
        self.assertIn(WorkgroupReleaseManager, registry)
        self.assertIn(WorkgroupSecurityGroup, registry)
        self.assertEqual(
            registry[PostOfficeEmailInbox].model._meta.app_label, "post_office"
        )
        self.assertEqual(
            registry[PostOfficeEmailOutbox].model._meta.app_label, "post_office"
        )
        self.assertEqual(
            registry[WorkgroupReleaseManager].model._meta.app_label, "post_office"
        )
        self.assertEqual(
            registry[WorkgroupSecurityGroup].model._meta.app_label, "post_office"
        )

    def test_admin_index_shows_post_office_group(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "6. Workgroup")
        self.assertContains(response, "Email Inboxes")
        self.assertContains(response, "Email Outboxes")
        self.assertContains(response, "Chat Profiles")
        self.assertContains(response, "Release Managers")
        self.assertContains(response, "Security Groups")
