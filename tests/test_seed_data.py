import os
import sys
import json
import shutil
import importlib.util
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase
from django.conf import settings
from integrator.models import RequestType


class SeedDataEntityTests(TestCase):
    def test_preserve_seed_data_on_create(self):
        rt = RequestType.objects.create(code="XYZ", name="Test", is_seed_data=True)
        self.assertTrue(RequestType.all_objects.get(pk=rt.pk).is_seed_data)


class EnvRefreshFixtureTests(TestCase):
    def test_env_refresh_marks_seed_data(self):
        base_dir = Path(settings.BASE_DIR)
        tmp_dir = base_dir / "temp_fixture"
        fixture_dir = tmp_dir / "fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        fixture_path = fixture_dir / "sample.json"
        fixture_path.write_text(
            json.dumps(
                [
                    {
                        "model": "integrator.requesttype",
                        "pk": 999,
                        "fields": {"code": "FTR", "name": "Fixture"},
                    }
                ]
            )
        )
        rel_path = str(fixture_path.relative_to(base_dir))
        spec = importlib.util.spec_from_file_location("env_refresh", base_dir / "env-refresh.py")
        env_refresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(env_refresh)
        env_refresh._fixture_files = lambda: [rel_path]
        from django.core.management import call_command as django_call

        def fake_call_command(name, *args, **kwargs):
            if name == "loaddata":
                django_call(name, *args, **kwargs)
            # ignore other commands

        env_refresh.call_command = fake_call_command
        env_refresh.run_database_tasks()
        rt = RequestType.all_objects.get(pk=999)
        self.assertTrue(rt.is_seed_data)
        shutil.rmtree(tmp_dir)
