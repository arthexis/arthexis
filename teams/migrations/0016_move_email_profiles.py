from django.conf import settings
from django.db import migrations, models
import core.fields
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("teams", "0015_merge_20251106_1941"),
        ("core", "0095_todo_created_on_backfill"),
        ("nodes", "0034_purge_net_messages"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="EmailInbox"),
                migrations.DeleteModel(name="EmailCollector"),
                migrations.DeleteModel(name="EmailOutbox"),
                migrations.CreateModel(
                    name="EmailInbox",
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
                            "username",
                            core.fields.SigilShortAutoField(
                                max_length=255,
                                help_text="Login name for the mailbox",
                            ),
                        ),
                        (
                            "host",
                            core.fields.SigilShortAutoField(
                                max_length=255,
                                help_text=(
                                    "Examples: Gmail IMAP 'imap.gmail.com', Gmail POP3 'pop.gmail.com',"
                                    " GoDaddy IMAP 'imap.secureserver.net', GoDaddy POP3 'pop.secureserver.net'"
                                ),
                            ),
                        ),
                        (
                            "port",
                            models.PositiveIntegerField(
                                default=993,
                                help_text=(
                                    "Common ports: Gmail IMAP 993, Gmail POP3 995, "
                                    "GoDaddy IMAP 993, GoDaddy POP3 995"
                                ),
                            ),
                        ),
                        (
                            "password",
                            core.fields.SigilShortAutoField(max_length=255),
                        ),
                        (
                            "protocol",
                            core.fields.SigilShortAutoField(
                                max_length=5,
                                choices=[("imap", "IMAP"), ("pop3", "POP3")],
                                default="imap",
                                help_text=(
                                    "IMAP keeps emails on the server for access across devices; "
                                    "POP3 downloads messages to a single device and may remove them from the server"
                                ),
                            ),
                        ),
                        ("use_ssl", models.BooleanField(default=True)),
                        (
                            "user",
                            models.OneToOneField(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="+",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                        (
                            "group",
                            models.OneToOneField(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="+",
                                to="core.securitygroup",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Email Inbox",
                        "verbose_name_plural": "Email Inboxes",
                        "db_table": "core_emailinbox",
                    },
                    bases=(core.models.Profile,),
                ),
                migrations.CreateModel(
                    name="EmailCollector",
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
                                max_length=255,
                                blank=True,
                                help_text="Optional label to identify this collector.",
                            ),
                        ),
                        (
                            "subject",
                            models.CharField(max_length=255, blank=True),
                        ),
                        ("sender", models.CharField(max_length=255, blank=True)),
                        ("body", models.CharField(max_length=255, blank=True)),
                        (
                            "fragment",
                            models.CharField(
                                max_length=255,
                                blank=True,
                                help_text="Pattern with [sigils] to extract values from the body.",
                            ),
                        ),
                        (
                            "use_regular_expressions",
                            models.BooleanField(
                                default=False,
                                help_text="Treat subject, sender and body filters as regular expressions (case-insensitive).",
                            ),
                        ),
                        (
                            "inbox",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="collectors",
                                to="teams.emailinbox",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Email Collector",
                        "verbose_name_plural": "Email Collectors",
                        "db_table": "core_emailcollector",
                    },
                    bases=(core.models.Entity,),
                ),
                migrations.CreateModel(
                    name="EmailOutbox",
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
                            "host",
                            core.fields.SigilShortAutoField(
                                max_length=100,
                                help_text="Gmail: smtp.gmail.com. GoDaddy: smtpout.secureserver.net",
                            ),
                        ),
                        (
                            "port",
                            models.PositiveIntegerField(
                                default=587,
                                help_text="Gmail: 587 (TLS). GoDaddy: 587 (TLS) or 465 (SSL)",
                            ),
                        ),
                        (
                            "username",
                            core.fields.SigilShortAutoField(
                                max_length=100,
                                blank=True,
                                help_text="Full email address for Gmail or GoDaddy",
                            ),
                        ),
                        (
                            "password",
                            core.fields.SigilShortAutoField(
                                max_length=100,
                                blank=True,
                                help_text="Email account password or app password",
                            ),
                        ),
                        (
                            "use_tls",
                            models.BooleanField(
                                default=True,
                                help_text="Check for Gmail or GoDaddy on port 587",
                            ),
                        ),
                        (
                            "use_ssl",
                            models.BooleanField(
                                default=False,
                                help_text="Check for GoDaddy on port 465; Gmail does not use SSL",
                            ),
                        ),
                        (
                            "from_email",
                            core.fields.SigilShortAutoField(
                                blank=True,
                                max_length=254,
                                verbose_name="From Email",
                                help_text="Default From address; usually the same as username",
                            ),
                        ),
                        (
                            "is_enabled",
                            models.BooleanField(
                                default=True,
                                help_text="Disable to remove this outbox from automatic selection.",
                            ),
                        ),
                        (
                            "group",
                            models.OneToOneField(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="+",
                                to="core.securitygroup",
                            ),
                        ),
                        (
                            "node",
                            models.OneToOneField(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="email_outbox",
                                to="nodes.node",
                            ),
                        ),
                        (
                            "user",
                            models.OneToOneField(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="+",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Email Outbox",
                        "verbose_name_plural": "Email Outboxes",
                        "db_table": "nodes_emailoutbox",
                    },
                    bases=(core.models.Profile,),
                ),
            ],
        )
    ]
