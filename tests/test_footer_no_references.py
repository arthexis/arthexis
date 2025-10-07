from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from core.models import PackageRelease, Reference
from core.release import DEFAULT_PACKAGE
from utils import revision


class FooterNoReferencesTests(TestCase):
    def test_footer_renders_without_references(self):
        Reference.objects.all().delete()
        response = self.client.get(reverse("pages:login"))
        self.assertContains(response, "<footer", html=False)
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
