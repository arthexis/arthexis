from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventenvelope",
            name="delivery_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="eventenvelope",
            name="delivery_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("dispatching", "Dispatching"),
                    ("published", "Published"),
                    ("failed", "Failed"),
                ],
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="eventenvelope",
            name="dispatch_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="eventenvelope",
            name="last_delivery_error",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="eventenvelope",
            name="next_delivery_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="eventenvelope",
            index=models.Index(
                fields=["delivery_status", "next_delivery_at", "created_at"],
                name="events_delivery_queue_idx",
            ),
        ),
    ]
