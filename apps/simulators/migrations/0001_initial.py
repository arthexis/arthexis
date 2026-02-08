from django.db import migrations, models
import django.db.models.deletion
import datetime


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("ocpp", "0020_charger_ftp_server"),
    ]

    operations = [
        migrations.CreateModel(
            name="SimulatorSchedule",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("is_seed_data", models.BooleanField(default=False, editable=False)),
                ("is_user_data", models.BooleanField(default=False, editable=False)),
                ("is_deleted", models.BooleanField(default=False, editable=False)),
                (
                    "name",
                    models.CharField(
                        blank=True, help_text="Optional label for this schedule.", max_length=120
                    ),
                ),
                (
                    "active",
                    models.BooleanField(
                        default=True, help_text="Enable this schedule for the simulator."
                    ),
                ),
                (
                    "schedule_date",
                    models.DateField(
                        blank=True,
                        help_text="Optional date for a one-off schedule; leave blank for daily runs.",
                        null=True,
                    ),
                ),
                (
                    "start_time",
                    models.TimeField(
                        default=datetime.time(0, 0),
                        help_text="Start of the daily scheduling window.",
                    ),
                ),
                (
                    "end_time",
                    models.TimeField(
                        default=datetime.time(23, 59),
                        help_text="End of the daily scheduling window.",
                    ),
                ),
                (
                    "run_count",
                    models.PositiveSmallIntegerField(
                        default=1,
                        help_text="Number of simulator runs to schedule inside the window.",
                    ),
                ),
                (
                    "randomize",
                    models.BooleanField(
                        default=False,
                        help_text="Randomize run start times within the window.",
                    ),
                ),
                (
                    "simulator",
                    models.ForeignKey(
                        help_text="Simulator configuration to run.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="schedules",
                        to="ocpp.simulator",
                    ),
                ),
            ],
            options={
                "verbose_name": "Simulator Schedule",
                "verbose_name_plural": "Simulator Schedules",
                "ordering": ["simulator", "schedule_date", "start_time"],
            },
        ),
    ]
