from django.db import migrations


def remove_summary_state(apps, schema_editor):
    Feature = apps.get_model("features", "Feature")
    NodeFeature = apps.get_model("nodes", "NodeFeature")

    Feature.objects.filter(slug="llm-summary-suite").delete()
    NodeFeature.objects.filter(slug="llm-summary").delete()

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS summary_llmsummaryconfig")


class Migration(migrations.Migration):
    dependencies = [
        ("nodes", "0003_remove_net_message_lcd_channels"),
        ("features", "0004_initial"),
    ]

    operations = [
        migrations.RunPython(remove_summary_state, migrations.RunPython.noop),
    ]
