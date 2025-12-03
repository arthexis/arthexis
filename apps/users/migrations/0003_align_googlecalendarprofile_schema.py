import apps.sigils.fields
import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_move_user_models"),
        ("users", "0002_totp_device_settings"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterModelOptions(
                    name="googlecalendarprofile",
                    options={
                        "verbose_name": "Google Calendar",
                        "verbose_name_plural": "Google Calendars",
                    },
                ),
                migrations.AlterModelOptions(
                    name="userphonenumber",
                    options={
                        "ordering": ("priority", "id"),
                        "verbose_name": "Phone Number",
                        "verbose_name_plural": "Phone Numbers",
                    },
                ),
                migrations.RemoveField(
                    model_name="googlecalendarprofile",
                    name="colors",
                ),
                migrations.RemoveField(
                    model_name="googlecalendarprofile",
                    name="event_age_days",
                ),
                migrations.AddField(
                    model_name="googlecalendarprofile",
                    name="timezone",
                    field=apps.sigils.fields.SigilShortAutoField(
                        blank=True, max_length=100, verbose_name="Time Zone"
                    ),
                ),
                migrations.AlterField(
                    model_name="googlecalendarprofile",
                    name="max_events",
                    field=models.PositiveIntegerField(
                        default=5,
                        help_text="Number of upcoming events to display (1-20).",
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(20),
                        ],
                    ),
                ),
                migrations.AlterField(
                    model_name="userphonenumber",
                    name="number",
                    field=models.CharField(
                        help_text="Contact phone number", max_length=32
                    ),
                ),
                migrations.AddConstraint(
                    model_name="googlecalendarprofile",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("user__isnull", False), ("group__isnull", True)),
                            models.Q(("user__isnull", True), ("group__isnull", False)),
                            _connector="OR",
                        ),
                        name="googlecalendarprofile_requires_owner",
                    ),
                ),
            ],
        )
    ]
