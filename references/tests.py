from django.template import Context, Template
from django.test import TestCase

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

from django.urls import reverse
from django.test import Client


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
    def test_footer_renders_selected_references(self):
        Reference.objects.create(
            value='https://example.com', alt_text='Example', include_in_footer=True
        )
        Reference.objects.create(value='https://ignored.com', alt_text='Ignore')
        html = Template("{% load ref_tags %}{% render_footer %}").render(Context())
        self.assertIn('https://example.com', html)
        self.assertIn('Example', html)
        self.assertNotIn('https://ignored.com', html)
