import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.apps import apps  # noqa: E402
from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402


def _local_app_labels() -> list[str]:
    base_dir = Path(settings.BASE_DIR)
    labels: list[str] = []
    for app_config in apps.get_app_configs():
        try:
            Path(app_config.path).relative_to(base_dir)
        except ValueError:
            continue
        labels.append(app_config.label)
    return labels


def test_project_has_no_pending_migrations():
    labels = _local_app_labels()
    call_command("makemigrations", *labels, check=True, dry_run=True)
