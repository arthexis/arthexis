from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("ocpp", "0025_compatibility_evidence")]
    operations = [
        migrations.CreateModel(
            name="ChargerTimelineProgress",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("state", models.CharField(choices=[("unknown", "Unknown"), ("historical", "Historical"), ("catching_up", "Catching up"), ("live", "Live")], default="unknown", max_length=16)),
                ("newest_event_at", models.DateTimeField(blank=True, null=True)),
                ("last_received_at", models.DateTimeField(blank=True, null=True)),
                ("historical_events_seen", models.PositiveBigIntegerField(default=0)),
                ("observed_events", models.PositiveBigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("charger", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="timeline_progress", to="ocpp.charger")),
            ],
        ),
    ]
