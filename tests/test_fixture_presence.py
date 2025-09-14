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
        self.assertTrue(Reference.objects.filter(include_in_footer=True).exists())

    def test_calculator_template_fixtures_exist(self):
        files = glob("awg/fixtures/calculator_templates__*.json")
        self.assertTrue(files, "CalculatorTemplate fixtures are missing")
        call_command("loaddata", *files)
        self.assertTrue(CalculatorTemplate.objects.exists())
