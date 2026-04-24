from django.db import migrations, models


DEFAULT_TENANT = "arthexis"


class Migration(migrations.Migration):
    dependencies = [
        ("netmesh", "0005_default_tenant_arthexis"),
    ]

    operations = [
        migrations.AlterField(
            model_name="meshmembership",
            name="tenant",
            field=models.CharField(
                blank=False,
                default=DEFAULT_TENANT,
                help_text="Tenant identifier for external mesh orchestration scope.",
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name="peerpolicy",
            name="tenant",
            field=models.CharField(
                blank=False,
                default=DEFAULT_TENANT,
                help_text="Tenant identifier that owns this policy.",
                max_length=64,
            ),
        ),
        migrations.AddConstraint(
            model_name="meshmembership",
            constraint=models.CheckConstraint(
                condition=~models.Q(tenant=""),
                name="netmesh_membership_tenant_non_empty",
            ),
        ),
        migrations.AddConstraint(
            model_name="peerpolicy",
            constraint=models.CheckConstraint(
                condition=~models.Q(tenant=""),
                name="netmesh_peerpolicy_tenant_non_empty",
            ),
        ),
    ]
