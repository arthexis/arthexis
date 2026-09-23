from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0020_protocol_operation_attempt_identity"),
    ]

    operations = [
        migrations.AddField(
            model_name="ocpptransaction",
            name="recovery_cleared_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ocpptransaction",
            name="recovery_clear_reason",
            field=models.CharField(blank=True, max_length=240),
        ),
        migrations.AlterField(
            model_name="ocpptransaction",
            name="recovery_state",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("unresolved", "Unresolved"),
                    ("cleared", "Operator cleared"),
                    ("completed", "Completed"),
                ],
                db_index=True,
                default="active",
                max_length=16,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="ocpptransaction",
            name="ocpp_transaction_recovery_state_consistent",
        ),
        migrations.AddConstraint(
            model_name="ocpptransaction",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(recovery_state="completed", stopped_at__isnull=False)
                    | models.Q(
                        recovery_state__in=("active", "unresolved", "cleared"),
                        stopped_at__isnull=True,
                    )
                ),
                name="ocpp_transaction_recovery_state_consistent",
            ),
        ),
    ]
