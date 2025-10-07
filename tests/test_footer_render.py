import os
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from unittest.mock import patch, PropertyMock

from core.models import PackageRelease, Reference
from core.release import DEFAULT_PACKAGE
from utils import revision
from nodes.models import (
    Node,
    NodeFeature,
    NodeFeatureAssignment,
    NodeRole,
)

TMP_MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TMP_MEDIA_ROOT)
class FooterRenderTests(TestCase):
    def setUp(self):
        Reference.objects.create(
            alt_text="Example",
            value="https://example.com",
            method="link",
            include_in_footer=True,
        )
        self.client = Client()

    def test_footer_contains_reference(self):
        response = self.client.get(reverse("pages:login"))
        self.assertContains(response, "<footer", html=False)
        self.assertContains(response, "Example")
        self.assertContains(response, "https://example.com")
        version = Path("VERSION").read_text().strip()
        revision_value = (revision.get_revision() or "").strip()
        release = PackageRelease.objects.filter(version=version).first()
        release_revision = ""
        if release and release.revision:
            release_revision = release.revision.strip()
        rev_short = ""
        if revision_value and revision_value != release_revision:
            rev_short = revision_value[-6:]

        release_name = f"{DEFAULT_PACKAGE.name}-{version}"
        if rev_short:
            release_name = f"{release_name}-{rev_short}"
        self.assertContains(response, release_name)

    def test_footer_private_visibility(self):
        Reference.objects.create(
            alt_text="Private",
            value="https://private.example.com",
            method="link",
            include_in_footer=True,
            footer_visibility=Reference.FOOTER_PRIVATE,
        )
        response = self.client.get(reverse("pages:login"))
        self.assertNotContains(response, "Private")
        user = get_user_model().objects.create_user(username="u1", password="x")
        self.client.force_login(user)
        response = self.client.get(reverse("pages:index"))
        self.assertContains(response, "Private")

    def test_footer_staff_visibility(self):
        Reference.objects.create(
            alt_text="Staff",
            value="https://staff.example.com",
            method="link",
            include_in_footer=True,
            footer_visibility=Reference.FOOTER_STAFF,
        )
        response = self.client.get(reverse("pages:login"))
        self.assertNotContains(response, "Staff")
        user = get_user_model().objects.create_user(username="u2", password="x")
        self.client.force_login(user)
        response = self.client.get(reverse("pages:index"))
        self.assertNotContains(response, "Staff")
        staff = get_user_model().objects.create_user(
            username="staff", password="x", is_staff=True
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("pages:index"))
        self.assertContains(response, "Staff")

    def test_footer_restrictions_require_match(self):
        terminal = NodeRole.objects.create(name="Terminal")
        kiosk = NodeRole.objects.create(name="Kiosk")
        feature_enabled = NodeFeature.objects.create(
            slug="feature-enabled", display="Feature Enabled"
        )
        feature_unmatched = NodeFeature.objects.create(
            slug="feature-unmatched", display="Feature Unmatched"
        )
        arthexis, _ = Site.objects.update_or_create(
            domain="arthexis.com", defaults={"name": "Arthexis"}
        )
        other_site, _ = Site.objects.update_or_create(
            domain="example.com", defaults={"name": "Example"}
        )

        with (
            patch("nodes.models.Node.refresh_features"),
            patch("nodes.models.Node.sync_feature_tasks"),
        ):
            node = Node.objects.create(
                hostname="node1",
                address="127.0.0.1",
                mac_address=None,
                public_endpoint="node1",
                role=terminal,
            )
            NodeFeatureAssignment.objects.create(node=node, feature=feature_enabled)

        role_ref = Reference.objects.create(
            alt_text="Role Only",
            value="https://role.example.com",
            include_in_footer=True,
        )
        role_ref.roles.add(terminal)

        feature_ref = Reference.objects.create(
            alt_text="Feature Only",
            value="https://feature.example.com",
            include_in_footer=True,
        )
        feature_ref.features.add(feature_enabled)

        site_ref = Reference.objects.create(
            alt_text="Site Only",
            value="https://site.example.com",
            include_in_footer=True,
        )
        site_ref.sites.add(arthexis)

        unmatched = Reference.objects.create(
            alt_text="Unmatched",
            value="https://unmatched.example.com",
            include_in_footer=True,
        )
        unmatched.roles.add(kiosk)
        unmatched.features.add(feature_unmatched)
        unmatched.sites.add(other_site)

        with patch("nodes.models.Node.get_local", return_value=node):
            response = self.client.get(reverse("pages:index"), HTTP_HOST="arthexis.com")

        self.assertContains(response, "Role Only")
        self.assertContains(response, "Feature Only")
        self.assertContains(response, "Site Only")
        self.assertNotContains(response, "Unmatched")

        with patch("nodes.models.Node.get_local", return_value=node):
            response = self.client.get(reverse("pages:index"), HTTP_HOST="testserver")

        self.assertContains(response, "Role Only")
        self.assertContains(response, "Feature Only")
        self.assertNotContains(response, "Site Only")

    def test_footer_feature_requires_active_assignment(self):
        feature = NodeFeature.objects.create(
            slug="feature-guarded",
            display="Feature Guarded",
        )
        role = NodeRole.objects.create(name="Feature Role")
        with (
            patch("nodes.models.Node.refresh_features"),
            patch("nodes.models.Node.sync_feature_tasks"),
        ):
            node = Node.objects.create(
                hostname="node-guarded",
                address="127.0.0.1",
                mac_address=None,
                public_endpoint="node-guarded",
                role=role,
            )
            assignment = NodeFeatureAssignment.objects.create(
                node=node, feature=feature
            )

        guarded = Reference.objects.create(
            alt_text="Guarded Link",
            value="https://guarded.example.com",
            include_in_footer=True,
        )
        guarded.features.add(feature)

        with patch("nodes.models.Node.get_local", return_value=node):
            response = self.client.get(reverse("pages:index"))
        self.assertContains(response, "Guarded Link")

        assignment.is_deleted = True
        assignment.save(update_fields=["is_deleted"])

        with patch("nodes.models.Node.get_local", return_value=node):
            response = self.client.get(reverse("pages:index"))
        self.assertNotContains(response, "Guarded Link")

    def test_footer_feature_requires_active_state(self):
        feature = NodeFeature.objects.create(
            slug="feature-inactive",
            display="Feature Inactive",
        )
        role = NodeRole.objects.create(name="Inactive Role")
        with (
            patch("nodes.models.Node.refresh_features"),
            patch("nodes.models.Node.sync_feature_tasks"),
        ):
            node = Node.objects.create(
                hostname="node-inactive",
                address="127.0.0.1",
                mac_address=None,
                public_endpoint="node-inactive",
                role=role,
            )
            NodeFeatureAssignment.objects.create(node=node, feature=feature)

        inactive_ref = Reference.objects.create(
            alt_text="Inactive Link",
            value="https://inactive.example.com",
            include_in_footer=True,
        )
        inactive_ref.features.add(feature)

        with patch.object(NodeFeature, "is_enabled", new_callable=PropertyMock) as mock:
            mock.return_value = False
            with patch("nodes.models.Node.get_local", return_value=node):
                response = self.client.get(reverse("pages:index"))
        self.assertNotContains(response, "Inactive Link")
