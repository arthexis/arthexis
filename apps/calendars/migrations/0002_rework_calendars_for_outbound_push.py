from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("calendars", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="CalendarEventDispatch",
        ),
        migrations.DeleteModel(
            name="CalendarEventSnapshot",
        ),
        migrations.DeleteModel(
            name="CalendarEventTrigger",
        ),
        migrations.AlterField(
            model_name="googlecalendar",
            name="account",
            field=models.ForeignKey(
                blank=True,
                help_text="Google account used to publish events to this calendar.",
                null=True,
                on_delete=models.SET_NULL,
                related_name="calendars",
                to="gdrive.googleaccount",
            ),
        ),
        migrations.AlterField(
            model_name="googlecalendar",
            name="calendar_id",
            field=models.CharField(
                help_text="Google Calendar ID that should receive outbound events.",
                max_length=255,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="googlecalendar",
            name="is_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Disable to prevent new outbound event pushes to this calendar.",
            ),
        ),
        migrations.AlterField(
            model_name="googlecalendar",
            name="metadata",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Optional deployment-owned metadata for outbound publishing.",
            ),
        ),
        migrations.AlterField(
            model_name="googlecalendar",
            name="name",
            field=models.CharField(
                help_text="Friendly display name for this outbound calendar destination.",
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="googlecalendar",
            name="timezone",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Default IANA timezone used when publishing events.",
                max_length=64,
            ),
        ),
    ]
