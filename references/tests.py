from pathlib import Path

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import Client, RequestFactory, TestCase
from django.urls import resolve, reverse

from .models import Reference


class ReferenceTests(TestCase):
    def test_template_tag_creates_reference(self):
        html = Template("{% load ref_tags %}{% ref_img 'https://arthexis.com' alt='Constellation' %}").render(Context())
        ref = Reference.objects.get(value='https://arthexis.com')
        self.assertIn(ref.image.url, html)
        self.assertEqual(ref.alt_text, 'Constellation')
        self.assertTrue(ref.image.path.endswith('.png'))
        self.assertEqual(ref.uses, 1)
        # calling again should not create another record but increment uses
        Template("{% load ref_tags %}{% ref_img 'https://arthexis.com' %}").render(Context())
        self.assertEqual(Reference.objects.filter(value='https://arthexis.com').count(), 1)
        ref.refresh_from_db()
        self.assertEqual(ref.uses, 2)

class ReferenceLandingPageTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_page_renders_and_generates(self):
        resp = self.client.get(reverse('references:generator'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<form')
        resp = self.client.get(reverse('references:generator'), {'data': 'hello'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data:image/png;base64')
        self.assertEqual(Reference.objects.count(), 0)


class FooterTemplateTagTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_footer_renders_selected_references(self):
        Reference.objects.create(
            value="https://arthexis.com", alt_text="Constellation", include_in_footer=True
        )
        Reference.objects.create(value="https://ignored.com", alt_text="Ignore")
        request = self.factory.get("/")
        html = Template("{% load ref_tags %}{% render_footer %}").render(
            Context({"request": request})
        )
        self.assertIn("https://arthexis.com", html)
        self.assertIn("Constellation", html)
        self.assertNotIn("https://ignored.com", html)
        self.assertIn("data:image/png;base64", html)
        rev = Path("REVISION").read_text().strip()[-8:]
        ver = Path("VERSION").read_text().strip()
        self.assertIn(f"ver {ver}", html)
        self.assertIn(f"rev {rev}", html)

    def test_footer_shows_admin_links_for_staff(self):
        Reference.objects.create(value="https://arthexis.com", include_in_footer=True)
        user = get_user_model().objects.create_user(
            "staff", "staff@arthexis.com", "pass", is_staff=True
        )
        request = self.factory.get("/ref/")
        request.user = user
        request.resolver_match = resolve("/ref/")
        html = Template("{% load ref_tags %}{% render_footer %}").render(
            Context({"request": request})
        )
        self.assertIn("References", html)
        self.assertIn(reverse("admin:references_reference_changelist"), html)

    def test_current_page_qr_tag(self):
        request = self.factory.get("/")
        html = Template("{% load ref_tags %}{% current_page_qr 50 %}").render(
            Context({"request": request})
        )
        self.assertIn("data:image/png;base64", html)


class ReferenceModelUpdateTests(TestCase):
    def test_qr_image_regenerates_on_value_change(self):
        ref = Reference.objects.create(value="https://old.com")
        old_name = ref.image.name
        ref.value = "https://new.com"
        ref.save()
        self.assertNotEqual(ref.image.name, old_name)


class ReferenceAdminDisplayTests(TestCase):
    def setUp(self):
        self.client = Client()
        user = get_user_model().objects.create_superuser(
            "refadmin", "admin@arthexis.com", "pass"
        )
        self.client.force_login(user)

    def test_change_form_displays_qr_code(self):
        ref = Reference.objects.create(value="https://arthexis.com")
        resp = self.client.get(
            reverse("admin:references_reference_change", args=[ref.pk])
        )
        self.assertContains(resp, f'src="{ref.image.url}"')

