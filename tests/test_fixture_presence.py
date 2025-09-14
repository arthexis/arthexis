from glob import glob

from django.core.management import call_command
from django.test import TestCase

from core.models import Reference
from awg.models import CalculatorTemplate


class FixturePresenceTests(TestCase):
    def test_footer_reference_fixtures_exist(self):
        files = glob("core/fixtures/references__*.json")
        self.assertTrue(files, "Reference fixtures are missing")
        call_command("loaddata", *files)
        refs = Reference.objects.filter(include_in_footer=True)
        expected = {
            "Arthexis Online": "https://arthexis.com",
            "Arthexis on PyPI": "https://pypi.org/project/arthexis/",
            "Gelectriic Solutions": "https://www.gelectriic.com",
            "Python": "https://www.python.org/",
            "Django": "https://www.djangoproject.com/",
            "OCPP": "https://openchargealliance.org/protocols/open-charge-point-protocol/",
            "Celery": "https://docs.celeryq.dev/en/stable/",
            "GitHub Repo": "https://github.com/arthexis/arthexis",
            "Odoo Partners": "https://www.odoo.com/partners",
            "Porsche Center": "https://dealer.porsche.com/mx/monterrey/es-MX",
            "MIT License": "https://en.wikipedia.org/wiki/MIT_License",
            "Mysteric Gallery": "https://ko-fi.com/arthexis/gallery",
        }
        actual = {ref.alt_text: ref.value for ref in refs}
        for alt_text, value in expected.items():
            self.assertEqual(
                actual.get(alt_text),
                value,
                f"Missing footer reference: {alt_text}",
            )

    def test_calculator_template_fixtures_exist(self):
        files = glob("awg/fixtures/calculator_templates__*.json")
        self.assertTrue(files, "CalculatorTemplate fixtures are missing")
        call_command("loaddata", *files)
        self.assertTrue(CalculatorTemplate.objects.exists())
