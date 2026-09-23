from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ocpp", "0022_ocpp_transaction_operator_cleared"),
    ]

    operations = [
        migrations.AddField(
            model_name="protocoloperation",
            name="reconciliation_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="protocoloperation",
            name="reconciled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="protocoloperation",
            name="reconciliation_resolution",
            field=models.CharField(
                blank=True,
                choices=[
                    ("achieved", "Desired state achieved"),
                    ("not_achieved", "Desired state not achieved"),
                    ("irrelevant", "No longer operationally relevant"),
                ],
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="protocoloperation",
            name="reconciliation_basis",
            field=models.CharField(blank=True, max_length=240),
        ),
    ]
