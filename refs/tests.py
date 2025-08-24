from pathlib import Path
from datetime import timedelta

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import Client, RequestFactory, TestCase
from django.urls import resolve, reverse

from .models import Reference
from .tagging import add_tag, get_tags


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
        self.user = get_user_model().objects.create_user(
            "tester", "tester@example.com", "pass"
        )

    def test_only_recent_references_are_listed(self):
        old_ref = Reference.objects.create(value="https://old.com", alt_text="Old")
        old_ref.created -= timedelta(hours=80)
        old_ref.save()
        recent_ref = Reference.objects.create(value="https://new.com", alt_text="New")
        resp = self.client.get(reverse('refs:recent'))
        self.assertContains(resp, "https://new.com")
        self.assertNotContains(resp, "https://old.com")

    def test_can_submit_new_reference(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse("refs:recent"),
            {"value": "https://form.com", "alt_text": "Form", "content_type": "text"},
        )
        self.assertEqual(resp.status_code, 302)
        ref = Reference.objects.get(value="https://form.com")
        self.assertEqual(ref.author, self.user)

    def test_anonymous_cannot_submit_reference(self):
        resp = self.client.post(
            reverse("refs:recent"),
            {"value": "https://anon.com", "alt_text": "Anon", "content_type": "text"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Reference.objects.filter(value="https://anon.com").exists())

    def test_form_hidden_for_anonymous(self):
        resp = self.client.get(reverse("refs:recent"))
        self.assertNotContains(resp, "New Reference")
        self.assertContains(resp, "You must be logged in")

    def test_long_text_shows_excerpt(self):
        long_text = "x" * 150
        Reference.objects.create(value=long_text, alt_text="Long", content_type="text")
        resp = self.client.get(reverse("refs:recent"))
        self.assertContains(resp, "Long")
        self.assertContains(resp, "xxx")

    def test_image_reference_shows_thumbnail(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        import io

        img_io = io.BytesIO()
        Image.new("RGB", (10, 10)).save(img_io, format="PNG")
        img = SimpleUploadedFile("test.png", img_io.getvalue(), content_type="image/png")
        Reference.objects.create(
            alt_text="Img", content_type="image", file=img
        )
        resp = self.client.get(reverse("refs:recent"))
        self.assertContains(resp, "<img", html=False)

    def test_url_value_rendered_as_link(self):
        Reference.objects.create(value="https://click.com", alt_text="Click")
        resp = self.client.get(reverse("refs:recent"))
        self.assertContains(resp, '<a href="https://click.com"', html=False)
        self.assertContains(resp, 'target="_blank"', html=False)


class FooterTemplateTagTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_footer_renders_selected_references(self):
        Reference.objects.create(
            value="https://example.com", alt_text="Example", include_in_footer=True
        )
        Reference.objects.create(value="https://ignored.com", alt_text="Ignore")
        request = self.factory.get("/")
        with patch("utils.revision.get_revision", return_value="abcdef123456"):
            html = Template("{% load ref_tags %}{% render_footer %}").render(
                Context({"request": request})
            )
        self.assertIn("https://example.com", html)
        self.assertIn("Example", html)
        self.assertNotIn("https://ignored.com", html)
        self.assertIn("data:image/png;base64", html)
        ver = Path("VERSION").read_text().strip()
        self.assertIn(f"ver {ver}", html)
        self.assertIn("rev 123456", html)

    def test_footer_shows_admin_links_for_staff(self):
        Reference.objects.create(
            value="https://example.com", alt_text="Example", include_in_footer=True
        )
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
        ref = Reference.objects.create(value="https://old.com", alt_text="Old")
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
        ref = Reference.objects.create(value="https://example.com", alt_text="Example")
        resp = self.client.get(
            reverse("admin:refs_reference_change", args=[ref.pk])
        )
        self.assertContains(resp, f'src="{ref.image.url}"')


class TaggingTests(TestCase):
    def test_add_and_get_tags(self):
        user_model = get_user_model()
        user = user_model.objects.create(username="tester")

        add_tag(user, "alpha")
        tags = list(get_tags(user))

        self.assertEqual(len(tags), 1)
        self.assertEqual(tags[0].name, "alpha")

