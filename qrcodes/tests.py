from django.template import Context, Template
from django.test import TestCase

from .models import QRLink


class QRLinkTests(TestCase):
    def test_template_tag_generates_qr(self):
        html = Template("{% load qr_tags %}{% qr_img 'https://example.com' %}").render(Context())
        qr = QRLink.objects.get(value='https://example.com')
        self.assertIn(qr.image.url, html)
        self.assertTrue(qr.image.path.endswith('.png'))
        # calling again should not create another record
        Template("{% load qr_tags %}{% qr_img 'https://example.com' %}").render(Context())
        self.assertEqual(QRLink.objects.filter(value='https://example.com').count(), 1)

from django.urls import reverse
from django.test import Client


class QRLandingPageTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_page_renders_and_generates(self):
        resp = self.client.get(reverse('qrcodes:generator'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<form')
        resp = self.client.get(reverse('qrcodes:generator'), {'data': 'hello'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data:image/png;base64')
        self.assertEqual(QRLink.objects.count(), 0)
