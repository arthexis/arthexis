from pathlib import Path
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import Client, RequestFactory, TestCase
from django.urls import resolve, reverse

from .models import Reference


class ReferenceTests(TestCase):
    def test_template_tag_creates_reference(self):
        html = Template("{% load ref_tags %}{% ref_img 'https://example.com' alt='Example' %}").render(Context())
        ref = Reference.objects.get(value='https://example.com')
        self.assertIn(ref.image.url, html)
        self.assertEqual(ref.alt_text, 'Example')
        self.assertTrue(ref.image.path.endswith('.png'))
        self.assertEqual(ref.uses, 1)
        # calling again should not create another record but increment uses
        Template("{% load ref_tags %}{% ref_img 'https://example.com' %}").render(Context())
        self.assertEqual(Reference.objects.filter(value='https://example.com').count(), 1)
        ref.refresh_from_db()
        self.assertEqual(ref.uses, 2)

class ReferenceLandingPageTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_page_renders_and_generates(self):
        resp = self.client.get(reverse('refs:generator'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<form')
        resp = self.client.get(reverse('refs:generator'), {'data': 'hello'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data:image/png;base64')
        self.assertEqual(Reference.objects.count(), 0)


class RecentReferencesTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_only_recent_references_are_listed(self):
        old_ref = Reference.objects.create(value="https://old.com")
        old_ref.created -= timedelta(hours=80)
        old_ref.save()
        recent_ref = Reference.objects.create(value="https://new.com")
        resp = self.client.get(reverse('refs:recent'))
        self.assertContains(resp, "https://new.com")
        self.assertNotContains(resp, "https://old.com")

    def test_can_submit_new_reference(self):
        resp = self.client.post(
            reverse("refs:recent"),
            {"value": "https://form.com", "alt_text": "Form"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Reference.objects.filter(value="https://form.com").exists())


class FooterTemplateTagTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_footer_renders_selected_references(self):
        Reference.objects.create(
            value="https://example.com", alt_text="Example", include_in_footer=True
        )
        Reference.objects.create(value="https://ignored.com", alt_text="Ignore")
        request = self.factory.get("/")
        html = Template("{% load ref_tags %}{% render_footer %}").render(
            Context({"request": request})
        )
        self.assertIn("https://example.com", html)
        self.assertIn("Example", html)
        self.assertNotIn("https://ignored.com", html)
        self.assertIn("data:image/png;base64", html)
        rev = Path("REVISION").read_text().strip()[-8:]
        ver = Path("VERSION").read_text().strip()
        self.assertIn(f"ver {ver}", html)
        self.assertIn(f"rev {rev}", html)

    def test_footer_shows_admin_links_for_staff(self):
        Reference.objects.create(value="https://example.com", include_in_footer=True)
        user = get_user_model().objects.create_user(
            "staff", "staff@example.com", "pass", is_staff=True
        )
        request = self.factory.get("/refs/")
        request.user = user
        request.resolver_match = resolve("/refs/")
        html = Template("{% load ref_tags %}{% render_footer %}").render(
            Context({"request": request})
        )
        self.assertIn("References", html)
        self.assertIn(reverse("admin:refs_reference_changelist"), html)

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
            "refadmin", "admin@example.com", "pass"
        )
        self.client.force_login(user)

    def test_change_form_displays_qr_code(self):
        ref = Reference.objects.create(value="https://example.com")
        resp = self.client.get(
            reverse("admin:refs_reference_change", args=[ref.pk])
        )
        self.assertContains(resp, f'src="{ref.image.url}"')

