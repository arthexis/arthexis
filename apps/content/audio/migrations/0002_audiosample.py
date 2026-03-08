from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("audio", "0001_initial"),
        ("content", "0004_webrequestsampler_webrequeststep_websample_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AudioSample",
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
                ("captured_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("duration_seconds", models.FloatField(blank=True, null=True)),
                ("sample_rate", models.PositiveIntegerField(blank=True, null=True)),
                ("channels", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("audio_format", models.CharField(blank=True, max_length=50)),
                (
                    "sample",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="audio_samples",
                        to="content.contentsample",
                    ),
                ),
            ],
            options={
                "verbose_name": "Audio Sample",
                "verbose_name_plural": "Audio Samples",
                "ordering": ["-captured_at", "-id"],
            },
        ),
    ]
