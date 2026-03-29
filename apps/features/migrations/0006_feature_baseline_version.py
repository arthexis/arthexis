from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db import migrations, models

from packaging.version import InvalidVersion, Version


def _parse_version(raw: str | None) -> Version | None:
    text = (raw or "").strip()
    if not text:
        return None
    text = text[1:] if text.lower().startswith("v") else text
    try:
        return Version(text)
    except InvalidVersion:
        return None


def _current_suite_version() -> Version | None:
    version_path = Path(settings.BASE_DIR) / "VERSION"
    if not version_path.exists():
        return None
    return _parse_version(version_path.read_text(encoding="utf-8"))


def _disable_future_baseline_features(apps, schema_editor):
    del schema_editor

    Feature = apps.get_model("features", "Feature")
    current_version = _current_suite_version()
    if current_version is None:
        return

    for feature in Feature.objects.exclude(baseline_version=""):
        baseline = _parse_version(feature.baseline_version)
        if baseline is None:
            continue
        if current_version >= baseline:
            continue
        if not feature.is_enabled:
            continue
        feature.is_enabled = False
        feature.save(update_fields=["is_enabled", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0005_merge_evergo_feature_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="feature",
            name="baseline_version",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional minimum Arthexis version where this suite feature should be enabled by default.",
                max_length=40,
            ),
        ),
        migrations.RunPython(_disable_future_baseline_features, migrations.RunPython.noop),
    ]
