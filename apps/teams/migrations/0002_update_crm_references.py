from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("teams", "0001_initial"),
        ("crms", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="manualtask",
                    name="odoo_products",
                    field=models.ManyToManyField(
                        blank=True,
                        help_text="Products associated with the requested work.",
                        related_name="manual_tasks",
                        to="crms.product",
                        verbose_name="Odoo products",
                    ),
                ),
                migrations.AlterField(
                    model_name="taskcategory",
                    name="odoo_products",
                    field=models.ManyToManyField(
                        blank=True,
                        help_text="Relevant Odoo products for this category.",
                        related_name="task_categories",
                        to="crms.product",
                        verbose_name="Odoo products",
                    ),
                ),
            ],
            database_operations=[],
        )
    ]
