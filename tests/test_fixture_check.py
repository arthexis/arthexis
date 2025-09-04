import os
import sys
import json
import hashlib
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase
from django.core import checks
from django.core.checks.registry import registry
from django.conf import settings


class FixtureCheckTests(TestCase):
    def setUp(self):
        self.base_dir = Path(settings.BASE_DIR)
        self.tmp_dir = self.base_dir / "temp_fixture_check"
        fixture_dir = self.tmp_dir / "fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        self.fixture_path = fixture_dir / "sample.json"
        self.fixture_path.write_text(
            json.dumps(
                [{"model": "nodes.noderole", "pk": 12345, "fields": {"name": "X"}}]
            )
        )
        self.hash_file = self.base_dir / "fixtures.md5"
        self.original_hash = self.hash_file.read_text().strip()

    def tearDown(self):
        self.hash_file.write_text(self.original_hash)
        self.fixture_path.unlink(missing_ok=True)
        (self.tmp_dir / "fixtures").rmdir()
        self.tmp_dir.rmdir()

    def run_check(self):
        return registry.run_checks(tags=[checks.Tags.database])

    def test_warning_for_new_fixture(self):
        msgs = self.run_check()
        self.assertTrue(any(m.id == "core.W001" for m in msgs))

    def test_no_warning_after_hash_update(self):
        md5 = hashlib.md5()
        for p in sorted(self.base_dir.glob("**/fixtures/*.json")):
            md5.update(p.read_bytes())
        self.hash_file.write_text(md5.hexdigest())
        msgs = self.run_check()
        self.assertFalse(any(m.id == "core.W001" for m in msgs))
