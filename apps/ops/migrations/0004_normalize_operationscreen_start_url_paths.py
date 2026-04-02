from urllib.parse import urlsplit

from django.db import migrations
from django.utils.http import escape_leading_slashes


def _is_local_absolute_path(candidate: str) -> bool:
    parts = urlsplit(candidate or "")
    if parts.scheme or parts.netloc:
        return False
    path = escape_leading_slashes(parts.path)
    return path.startswith("/")


def normalize_start_urls(apps, schema_editor):
    OperationScreen = apps.get_model("ops", "OperationScreen")
    invalid_ids = [
        operation.pk
        for operation in OperationScreen.objects.all().only("id", "start_url")
        if not _is_local_absolute_path(operation.start_url)
    ]
    if invalid_ids:
        OperationScreen.objects.filter(pk__in=invalid_ids).update(start_url="/admin/")


class Migration(migrations.Migration):

    dependencies = [
        ("ops", "0003_alter_operationscreen_start_url"),
    ]

    operations = [
        migrations.RunPython(normalize_start_urls, migrations.RunPython.noop),
    ]
