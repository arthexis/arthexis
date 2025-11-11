from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0095_todo_created_on_backfill"),
        ("teams", "0016_move_email_profiles"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="EmailCollector"),
                migrations.DeleteModel(name="EmailInbox"),
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="emailartifact",
                    name="collector",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="artifacts",
                        to="teams.emailcollector",
                    ),
                )
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="emailtransaction",
                    name="collector",
                    field=models.ForeignKey(
                        blank=True,
                        help_text="Collector that discovered this message, if applicable.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="transactions",
                        to="teams.emailcollector",
                    ),
                ),
                migrations.AlterField(
                    model_name="emailtransaction",
                    name="inbox",
                    field=models.ForeignKey(
                        blank=True,
                        help_text="Inbox account the message was read from or will use for sending.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="transactions",
                        to="teams.emailinbox",
                    ),
                ),
                migrations.AlterField(
                    model_name="emailtransaction",
                    name="outbox",
                    field=models.ForeignKey(
                        blank=True,
                        help_text="Outbox configuration used to send the message, when known.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="transactions",
                        to="teams.emailoutbox",
                    ),
                ),
            ],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="invitelead",
                    name="sent_via_outbox",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="invite_leads",
                        to="teams.emailoutbox",
                    ),
                )
            ],
        ),
    ]
