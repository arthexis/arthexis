from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("odoo", "0003_remove_odoochatbridge_unique_odoo_chat_bridge_site_and_more"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="OdooProfile",
            new_name="OdooEmployee",
        ),
        migrations.RemoveConstraint(
            model_name="odooemployee",
            name="odooprofile_requires_owner",
        ),
        migrations.AlterModelTable(
            name="odooemployee",
            table="core_odooemployee",
        ),
        migrations.AlterModelOptions(
            name="odooemployee",
            options={
                "verbose_name": "Odoo Employee",
                "verbose_name_plural": "Odoo Employees",
            },
        ),
        migrations.AddConstraint(
            model_name="odooemployee",
            constraint=models.CheckConstraint(
                condition=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="odooemployee_requires_owner",
            ),
        ),
    ]
