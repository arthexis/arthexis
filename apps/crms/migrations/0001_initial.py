from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import apps.core.fields


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("sigils", "0001_initial"),
        ("core", "0104_delete_sigilroot"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="Product",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        ("name", models.CharField(max_length=100)),
                        ("description", models.TextField(blank=True)),
                        ("renewal_period", models.PositiveIntegerField(help_text="Renewal period in days")),
                        (
                            "odoo_product",
                            models.JSONField(
                                blank=True,
                                help_text="Selected product from Odoo (id and name)",
                                null=True,
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Product",
                        "verbose_name_plural": "Products",
                        "db_table": "core_product",
                    },
                ),
                migrations.CreateModel(
                    name="OdooProfile",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        ("host", apps.core.fields.SigilShortAutoField(max_length=255)),
                        ("database", apps.core.fields.SigilShortAutoField(max_length=255)),
                        ("username", apps.core.fields.SigilShortAutoField(max_length=255)),
                        ("password", apps.core.fields.SigilShortAutoField(max_length=255)),
                        (
                            "crm",
                            models.CharField(
                                choices=[("odoo", "Odoo")],
                                default="odoo",
                                max_length=32,
                            ),
                        ),
                        ("verified_on", models.DateTimeField(blank=True, null=True)),
                        (
                            "odoo_uid",
                            models.PositiveIntegerField(blank=True, editable=False, null=True),
                        ),
                        ("name", models.CharField(blank=True, editable=False, max_length=255)),
                        (
                            "email",
                            models.EmailField(blank=True, editable=False, max_length=254),
                        ),
                        (
                            "partner_id",
                            models.PositiveIntegerField(blank=True, editable=False, null=True),
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
                        "verbose_name": "CRM Employee",
                        "verbose_name_plural": "CRM Employees",
                        "db_table": "core_odooprofile",
                    },
                ),
                migrations.AddConstraint(
                    model_name="odooprofile",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("user__isnull", False), ("group__isnull", True)),
                            models.Q(("user__isnull", True), ("group__isnull", False)),
                            _connector="OR",
                        ),
                        name="odooprofile_requires_owner",
                    ),
                ),
            ],
            database_operations=[],
        )
    ]
