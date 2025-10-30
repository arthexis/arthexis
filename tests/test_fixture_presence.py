import json
from glob import glob

from django.core.management import call_command
from django.test import TestCase

from core.models import Reference
from awg.models import CalculatorTemplate


def _load_fixture(path):
    with open(path, "r", encoding="utf-8") as fixture_file:
        return json.load(fixture_file)


class FixturePresenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reference_fixtures = sorted(
            glob("core/fixtures/references__*.json")
        )
        cls.calculator_fixtures = sorted(
            glob("awg/fixtures/calculator_templates__*.json")
        )
        if cls.reference_fixtures:
            call_command("loaddata", *cls.reference_fixtures)
        if cls.calculator_fixtures:
            call_command("loaddata", *cls.calculator_fixtures)

    def test_footer_reference_fixtures_exist(self):
        self.assertTrue(self.reference_fixtures, "Reference fixtures are missing")
        self.assertTrue(Reference.objects.filter(include_in_footer=True).exists())

    def test_calculator_template_fixtures_exist(self):
        self.assertTrue(
            self.calculator_fixtures,
            "CalculatorTemplate fixtures are missing",
        )
        self.assertTrue(CalculatorTemplate.objects.exists())

    def test_package_release_fixtures_use_natural_keys(self):
        fixtures = glob("core/fixtures/releases__packagerelease_*.json")
        self.assertTrue(fixtures, "PackageRelease fixtures are missing")
        for fixture_path in fixtures:
            records = _load_fixture(fixture_path)
            self.assertTrue(
                records,
                msg=f"No records found in {fixture_path}",
            )
            for record in records:
                self.assertNotIn(
                    "pk",
                    record,
                    msg=f"Unexpected primary key in {fixture_path}",
                )
                package_key = record["fields"].get("package")
                self.assertIsInstance(
                    package_key,
                    list,
                    msg=f"Package natural key must be a list in {fixture_path}",
                )
                self.assertTrue(
                    package_key,
                    msg=f"Package natural key is empty in {fixture_path}",
                )
