from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0005_remove_dashboardrule"),
        ("links", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="ExperienceReference"),
                migrations.CreateModel(
                    name="ExperienceReference",
                    fields=[],
                    options={
                        "verbose_name": "Reference",
                        "verbose_name_plural": "References",
                        "proxy": True,
                        "indexes": [],
                        "constraints": [],
                    },
                    bases=("links.Reference",),
                ),
            ],
        )
    ]
