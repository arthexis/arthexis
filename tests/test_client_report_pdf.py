from datetime import date
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import ClientReport


class ClientReportPdfTemplateTests(TestCase):
    def test_load_pdf_template_returns_expected_labels(self):
        labels = ClientReport._load_pdf_template("es")
        self.assertEqual(labels.get("report_totals"), "Totales del informe")
        self.assertEqual(labels.get("charge_point"), "Punto de carga")


class ClientReportPdfRenderingTests(TestCase):
    def test_render_pdf_uses_language_template(self):
        user = get_user_model().objects.create_user(
            username="pdf-user", password="pwd"
        )
        report = ClientReport.objects.create(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            data={
                "schema": "evcs-session/v1",
                "evcs": [
                    {
                        "display_name": "",
                        "serial_number": "XYZ",
                        "total_kw": 10,
                        "total_kw_period": 5,
                        "transactions": [],
                    }
                ],
                "totals": {"total_kw": 10, "total_kw_period": 5},
            },
            owner=user,
            disable_emails=True,
            language="es",
            title="",
        )

        paragraphs: list[str] = []

        def fake_paragraph(text, style):
            paragraphs.append(text)
            return MagicMock()

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "report.pdf"
            with (
                patch("reportlab.platypus.SimpleDocTemplate") as simple_doc_template,
                patch("reportlab.platypus.Paragraph", side_effect=fake_paragraph),
                patch("reportlab.platypus.Table"),
                patch("reportlab.platypus.TableStyle"),
            ):
                document = MagicMock()
                document.width = 400
                simple_doc_template.return_value = document
                document.build.return_value = None

                report.render_pdf(target)

        self.assertTrue(any("Informe de consumo" in text for text in paragraphs))
        self.assertTrue(any("Período:" in text for text in paragraphs))
        self.assertTrue(any("(Serie: XYZ)" in text for text in paragraphs))
        self.assertTrue(any("Totales del informe" in text for text in paragraphs))
        self.assertTrue(any("kW totales" in text for text in paragraphs))
