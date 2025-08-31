from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from core.models import Reference
from core.release import DEFAULT_PACKAGE
from utils import revision


class FooterNoReferencesTests(TestCase):
    def test_footer_renders_without_references(self):
        Reference.objects.all().delete()
        response = self.client.get(reverse("pages:login"))
        self.assertContains(response, "<footer", html=False)
        version = Path("VERSION").read_text().strip()
        rev_short = revision.get_revision()[-6:]
        release_name = f"{DEFAULT_PACKAGE.name}-{version}"
        if rev_short:
            release_name = f"{release_name}-{rev_short}"
        self.assertContains(response, release_name)
