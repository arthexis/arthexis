import django.db.models.deletion
from django.db import migrations, models


EMAIL_MODEL_NAMES = (
    "emailartifact",
    "emailtransaction",
    "emailtransactionattachment",
)


def _move_content_types(apps, source_label: str, target_label: str) -> None:
    """Move legacy content types without changing their primary keys.

    Preserving the ContentType row identity keeps existing permissions, admin log
    entries, and generic relations attached to the same logical model. On a fresh
    database these rows do not exist until post_migrate, so there is nothing to do.
    """

    ContentType = apps.get_model("contenttypes", "ContentType")
    for model_name in EMAIL_MODEL_NAMES:
        source = ContentType.objects.filter(
            app_label=source_label, model=model_name
        ).first()
        if source is None:
            continue

        target = ContentType.objects.filter(
            app_label=target_label, model=model_name
        ).first()
        if target is not None and target.pk != source.pk:
            raise RuntimeError(
                "Cannot transfer email ContentType ownership because both "
                f"{source_label}.{model_name} and {target_label}.{model_name} exist."
            )

        source.app_label = target_label
        source.save(update_fields=["app_label"])


def move_core_content_types_to_emails(apps, schema_editor):
    del schema_editor
    _move_content_types(apps, "core", "emails")


def move_email_content_types_back_to_core(apps, schema_editor):
    del schema_editor
    _move_content_types(apps, "emails", "core")


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0002_initial"),
        ("emails", "0003_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    move_core_content_types_to_emails,
                    move_email_content_types_back_to_core,
                )
            ],
            state_operations=[
                migrations.CreateModel(
                    name="EmailArtifact",
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
                        ("subject", models.CharField(max_length=255)),
                        ("sender", models.CharField(max_length=255)),
                        ("body", models.TextField(blank=True)),
                        ("sigils", models.JSONField(default=dict)),
                        ("fingerprint", models.CharField(max_length=32)),
                        (
                            "collector",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="artifacts",
                                to="emails.emailcollector",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Email Artifact",
                        "verbose_name_plural": "Email Artifacts",
                        "ordering": ["-id"],
                        "db_table": "core_emailartifact",
                        "unique_together": {("collector", "fingerprint")},
                    },
                ),
                migrations.CreateModel(
                    name="EmailTransaction",
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
                            "direction",
                            models.CharField(
                                choices=[("inbound", "Inbound"), ("outbound", "Outbound")],
                                default="inbound",
                                help_text="Whether the message originated from an inbox or is being sent out.",
                                max_length=8,
                            ),
                        ),
                        (
                            "status",
                            models.CharField(
                                choices=[
                                    ("collected", "Collected"),
                                    ("queued", "Queued"),
                                    ("sent", "Sent"),
                                    ("failed", "Failed"),
                                ],
                                default="collected",
                                help_text="Lifecycle stage for the stored email message.",
                                max_length=9,
                            ),
                        ),
                        (
                            "message_id",
                            models.CharField(
                                blank=True,
                                help_text="Message-ID header for threading and deduplication.",
                                max_length=255,
                            ),
                        ),
                        (
                            "thread_id",
                            models.CharField(
                                blank=True,
                                help_text="Thread or conversation identifier, if provided by the provider.",
                                max_length=255,
                            ),
                        ),
                        ("subject", models.CharField(blank=True, max_length=998)),
                        (
                            "from_address",
                            models.CharField(
                                blank=True,
                                help_text="From header as provided by the email message.",
                                max_length=512,
                            ),
                        ),
                        (
                            "sender_address",
                            models.CharField(
                                blank=True,
                                help_text="Envelope sender address, if available.",
                                max_length=512,
                            ),
                        ),
                        (
                            "to_addresses",
                            models.JSONField(
                                blank=True,
                                default=list,
                                help_text="List of To recipient addresses.",
                            ),
                        ),
                        (
                            "cc_addresses",
                            models.JSONField(
                                blank=True,
                                default=list,
                                help_text="List of Cc recipient addresses.",
                            ),
                        ),
                        (
                            "bcc_addresses",
                            models.JSONField(
                                blank=True,
                                default=list,
                                help_text="List of Bcc recipient addresses.",
                            ),
                        ),
                        (
                            "reply_to_addresses",
                            models.JSONField(
                                blank=True,
                                default=list,
                                help_text="List of Reply-To addresses from the message headers.",
                            ),
                        ),
                        (
                            "headers",
                            models.JSONField(
                                blank=True,
                                default=dict,
                                help_text="Complete header map as parsed from the message.",
                            ),
                        ),
                        (
                            "metadata",
                            models.JSONField(
                                blank=True,
                                default=dict,
                                help_text="Additional provider-specific metadata.",
                            ),
                        ),
                        ("body_text", models.TextField(blank=True)),
                        ("body_html", models.TextField(blank=True)),
                        (
                            "raw_content",
                            models.TextField(
                                blank=True,
                                help_text="Raw RFC822 payload for the message, if stored.",
                            ),
                        ),
                        (
                            "message_ts",
                            models.DateTimeField(
                                blank=True,
                                help_text="Timestamp supplied by the email headers.",
                                null=True,
                            ),
                        ),
                        (
                            "queued_at",
                            models.DateTimeField(
                                blank=True,
                                help_text="When the message was queued for outbound delivery.",
                                null=True,
                            ),
                        ),
                        (
                            "processed_at",
                            models.DateTimeField(
                                blank=True,
                                help_text="When the message was sent or fully processed.",
                                null=True,
                            ),
                        ),
                        (
                            "error",
                            models.TextField(
                                blank=True,
                                help_text="Failure details captured during processing, if any.",
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        (
                            "collector",
                            models.ForeignKey(
                                blank=True,
                                help_text="Collector that discovered this message, if applicable.",
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="transactions",
                                to="emails.emailcollector",
                            ),
                        ),
                        (
                            "inbox",
                            models.ForeignKey(
                                blank=True,
                                help_text="Inbox account the message was read from or will use for sending.",
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="transactions",
                                to="emails.emailinbox",
                            ),
                        ),
                        (
                            "outbox",
                            models.ForeignKey(
                                blank=True,
                                help_text="Outbox configuration used to send the message, when known.",
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="transactions",
                                to="emails.emailoutbox",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Email Transaction",
                        "verbose_name_plural": "Email Transactions",
                        "ordering": ["-created_at", "-id"],
                        "db_table": "core_emailtransaction",
                        "indexes": [
                            models.Index(fields=["message_id"], name="email_txn_msgid"),
                            models.Index(
                                fields=["direction", "status"],
                                name="email_txn_dir_status",
                            ),
                        ],
                    },
                ),
                migrations.CreateModel(
                    name="EmailTransactionAttachment",
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
                        ("filename", models.CharField(blank=True, max_length=255)),
                        ("content_type", models.CharField(blank=True, max_length=255)),
                        (
                            "content_id",
                            models.CharField(
                                blank=True,
                                help_text="Identifier used for inline attachments.",
                                max_length=255,
                            ),
                        ),
                        (
                            "inline",
                            models.BooleanField(
                                default=False,
                                help_text="Marks whether the attachment is referenced inline in the body.",
                            ),
                        ),
                        (
                            "size",
                            models.PositiveIntegerField(
                                blank=True,
                                help_text="Size of the decoded attachment payload in bytes.",
                                null=True,
                            ),
                        ),
                        (
                            "content",
                            models.TextField(
                                blank=True,
                                help_text="Base64 encoded attachment payload.",
                            ),
                        ),
                        (
                            "transaction",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="attachments",
                                to="emails.emailtransaction",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Email Attachment",
                        "verbose_name_plural": "Email Attachments",
                        "db_table": "core_emailtransactionattachment",
                    },
                ),
            ],
        )
    ]
