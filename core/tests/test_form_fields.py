from __future__ import annotations

import base64

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from core.form_fields import Base64FileField
from core.widgets import AdminBase64FileWidget


class Base64FileFieldTests(SimpleTestCase):
    def test_uploaded_file_is_encoded(self):
        field = Base64FileField()
        upload = SimpleUploadedFile("manual.pdf", b"example")
        cleaned = field.clean(upload)
        self.assertEqual(cleaned, base64.b64encode(b"example").decode("ascii"))

    def test_initial_value_is_preserved(self):
        initial = base64.b64encode(b"initial").decode("ascii")
        field = Base64FileField()
        cleaned = field.clean(None, initial)
        self.assertEqual(cleaned, initial)

    def test_clearing_field_returns_empty_string(self):
        field = Base64FileField(required=False)
        cleaned = field.clean(False, base64.b64encode(b"initial").decode("ascii"))
        self.assertEqual(cleaned, "")


class AdminBase64FileWidgetTests(SimpleTestCase):
    def test_context_exposes_download_information(self):
        widget = AdminBase64FileWidget(download_name="manual.pdf", content_type="application/pdf")
        encoded = base64.b64encode(b"pdf").decode("ascii")
        context = widget.get_context("content_pdf", encoded, {"id": "id_content_pdf"})
        widget_context = context["widget"]
        self.assertTrue(widget_context["is_initial"])
        self.assertEqual(widget_context["download_name"], "manual.pdf")
        self.assertEqual(widget_context["content_type"], "application/pdf")
        self.assertEqual(widget_context["base64_value"], encoded)
