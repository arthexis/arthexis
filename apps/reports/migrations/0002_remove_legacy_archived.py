from django.db import migrations, models


LEGACY_REPORT_TYPE = "legacy_archived"


def require_legacy_reports_exported(apps, schema_editor):
    SQLReport = apps.get_model("reports", "SQLReport")
    count = SQLReport.objects.filter(report_type=LEGACY_REPORT_TYPE).count()
    if count:
        raise RuntimeError(
            f"Cannot remove legacy report storage while {count} archived report(s) remain. "
            "Run `python manage.py export_legacy_reports <path>.json --delete` "
            "and keep the exported JSON before retrying the migration."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            require_legacy_reports_exported,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="sqlreport",
            name="legacy_definition",
        ),
        migrations.AlterField(
            model_name="sqlreport",
            name="report_type",
            field=models.CharField(
                choices=[
                    ("report_product_activity", "Report product activity"),
                    ("scheduled_reports", "Scheduled reports overview"),
                    ("sigil_roots", "Sigil roots catalog"),
                ],
                max_length=64,
            ),
        ),
    ]
