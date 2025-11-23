from django.test import TestCase
from django.urls import reverse

from core.models import Reference


class FooterNoReferencesTests(TestCase):
    def test_footer_renders_without_references(self):
        Reference.objects.all().delete()
        response = self.client.get(reverse("pages:login"))
        self.assertNotContains(response, "<footer", html=False)
